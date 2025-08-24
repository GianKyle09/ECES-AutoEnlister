from flask import Flask, render_template, request, url_for, redirect, session, flash, abort
from flask_socketio import SocketIO
import subprocess
import os
import threading
import time
import datetime
import database
import hmac
import hashlib
import json

app = Flask(__name__)
app.config['SECRET_KEY'] = 'a_very_secret_key_for_production'
app.config['ADMIN_PASSWORD'] = 'changethispassword' # IMPORTANT: Change this in production
app.config['GITHUB_WEBHOOK_SECRET'] = 'a_strong_and_secret_webhook_secret' # IMPORTANT: Change this

# Use eventlet for non-blocking I/O, which is crucial for streaming
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="https://enlist.eces.ovh")

# Dictionary to hold running processes, keyed by license key
processes = {}

def license_manager():
    """A background thread that monitors active licenses and terminates them on expiry."""
    with app.app_context():
        while True:
            active_keys = database.get_active_keys()
            for key_data in active_keys:
                if key_data['duration_minutes'] == -1: # Lifetime key
                    continue

                start_time = key_data['start_time']
                duration = datetime.timedelta(minutes=key_data['duration_minutes'])
                
                if start_time + duration < datetime.datetime.now():
                    print(f"License key {key_data['key']} has expired. Terminating process.")
                    if key_data['key'] in processes and processes[key_data['key']]['process'].poll() is None:
                        processes[key_data['key']]['process'].terminate()
                        # We can't easily emit to a disconnected user, but we log it.
                    database.deactivate_key(key_data['key'])
                else:
                    # Send time remaining update to any connected user for this key
                    remaining = (start_time + duration) - datetime.datetime.now()
                    remaining_str = str(remaining).split('.')[0]
                    if key_data['key'] in processes:
                        sid = processes[key_data['key']].get('sid')
                        if sid:
                            socketio.emit('license_status', {'time_remaining': remaining_str}, room=sid)

            time.sleep(1) # Check every second for a responsive timer

def stream_output(process, license_key):
    """Reads output and prepares it for broadcasting."""
    try:
        for line in iter(process.stdout.readline, ''):
            # Instead of emitting directly, we store the line to be broadcasted
            # This part will be handled by a broadcaster thread or similar mechanism
            # For simplicity in this step, we'll just print to server log
            # A full implementation would use a queue that the broadcaster reads from.
            
            # Find the session ID to emit to, if a user is connected
            sid = processes.get(license_key, {}).get('sid')
            if sid:
                line = line.strip()
                if line.startswith('JSON_DATA::'):
                    try:
                        json_str = line.split('::', 1)[1]
                        data = json.loads(json_str)
                        socketio.emit('update_tables', data, room=sid)
                    except (json.JSONDecodeError, IndexError) as e:
                        print(f"Error decoding JSON from script: {e}")
                else:
                    socketio.emit('terminal_output', {'data': line}, room=sid)

        process.stdout.close()
        return_code = process.wait()
        
        sid = processes.get(license_key, {}).get('sid')
        if sid:
            socketio.emit('script_finished', {'code': return_code}, room=sid)

    except Exception as e:
        print(f"Stream output thread error for key {license_key}: {e}")
    finally:
        # Clean up the process from the dictionary
        if license_key in processes:
            database.deactivate_key(license_key)
            del processes[license_key]

@app.route('/')
def index():
    """Serves the main HTML page."""
    return render_template('index.html')

@app.route('/webhook/deploy', methods=['POST'])
def webhook_deploy():
    """Receives and verifies the GitHub webhook for automated deployment."""
    # Verify the signature
    signature = request.headers.get('X-Hub-Signature-256')
    if not signature:
        abort(400, 'Signature header is missing')

    sha_name, signature_hash = signature.split('=', 1)
    if sha_name != 'sha256':
        abort(400, 'Signature must be sha256')

    mac = hmac.new(app.config['GITHUB_WEBHOOK_SECRET'].encode('utf-8'), msg=request.data, digestmod=hashlib.sha256)
    if not hmac.compare_digest(mac.hexdigest(), signature_hash):
        abort(403, 'Invalid signature')

    # If the signature is valid, create the trigger file
    if request.json and request.json.get('ref') == 'refs/heads/main':
        trigger_path = os.path.join(os.path.dirname(__file__), '.deployment_trigger')
        with open(trigger_path, 'w') as f:
            f.write('deploy')
        print("Deployment triggered by GitHub webhook.")
        return 'OK', 200
    
    return 'Push was not to the main branch', 200

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Handles the admin login."""
    error = None
    if request.method == 'POST':
        if request.form.get('password') == app.config['ADMIN_PASSWORD']:
            session['admin_logged_in'] = True
            return redirect(url_for('admin'))
        else:
            error = 'Invalid password'
    return render_template('admin_login.html', error=error)

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    """Serves the admin panel, protected by login."""
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    new_key = None
    if request.method == 'POST':
        is_lifetime = 'lifetime' in request.form
        duration = -1 if is_lifetime else int(request.form.get('duration', 60))
        allowed_id = request.form.get('allowed_id')
        new_key = database.generate_key(duration, allowed_id)

    all_keys = database.get_all_keys()
    for key in all_keys:
        if key['is_active'] and key['duration_minutes'] != -1:
            start_time = datetime.datetime.fromisoformat(key['start_time'])
            duration = datetime.timedelta(minutes=key['duration_minutes'])
            remaining = (start_time + duration) - datetime.datetime.now()
            if remaining.total_seconds() > 0:
                key['time_remaining'] = str(remaining).split('.')[0]
            else:
                key['time_remaining'] = 'Expired'
    
    return render_template('admin.html', new_key=new_key, keys=all_keys)

@app.route('/admin/delete', methods=['POST'])
def delete_key():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    key_id = request.form.get('key_id')
    database.delete_key(key_id)
    return redirect(url_for('admin'))

@app.route('/admin/extend', methods=['POST'])
def extend_key():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    key_id = request.form.get('key_id')
    minutes = int(request.form.get('minutes', 0))
    database.extend_key_duration(key_id, minutes)
    return redirect(url_for('admin'))

@app.route('/admin/lifetime', methods=['POST'])
def lifetime_key():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    key_id = request.form.get('key_id')
    database.make_key_lifetime(key_id)
    return redirect(url_for('admin'))

@socketio.on('start_script')
def handle_start_script(data):
    """Validates the license key and starts the scraper script."""
    sid = request.sid
    license_key = data.get('license_key')

    if not license_key:
        socketio.emit('terminal_output', {'data': 'Error: License key is required.'}, room=sid)
        return

    if license_key in processes and processes[license_key]['process'].poll() is None:
        socketio.emit('terminal_output', {'data': '--- Script is already running for this license. Reconnecting... ---'}, room=sid)
        processes[license_key]['sid'] = sid # Re-associate the new session ID
        return

    id_number = data.get('id_number')
    password = data.get('password')
    receiver_email = data.get('receiver_email')
    license_key = data.get('license_key')

    if not all([id_number, password, receiver_email, license_key]):
        socketio.emit('terminal_output', {'data': 'Error: All fields are required.'}, room=sid)
        socketio.emit('script_finished', {'code': 'Validation Error'})
        return

    # --- License Key Validation ---
    key_data = database.get_key(license_key)
    if not key_data:
        socketio.emit('terminal_output', {'data': '--- ERROR: Invalid license key. ---'}, room=sid)
        socketio.emit('script_finished', {'code': 'Invalid Key'})
        return
    
    if key_data['is_active']:
        socketio.emit('terminal_output', {'data': '--- ERROR: License key is already in use. ---'}, room=sid)
        socketio.emit('script_finished', {'code': 'Key in Use'})
        return

    if key_data['allowed_id_number'] and key_data['allowed_id_number'] != id_number:
        socketio.emit('terminal_output', {'data': '--- ERROR: This license key is not valid for the provided ID number. ---'}, room=sid)
        socketio.emit('script_finished', {'code': 'ID Mismatch'})
        return
    
    # Check if a timed key has expired (even if not marked active)
    if key_data['start_time'] and key_data['duration_minutes'] != -1:
        start_time = datetime.datetime.fromisoformat(key_data['start_time'])
        if datetime.datetime.now() > start_time + datetime.timedelta(minutes=key_data['duration_minutes']):
            socketio.emit('terminal_output', {'data': '--- ERROR: This license key has expired. ---'}, room=sid)
            socketio.emit('script_finished', {'code': 'Expired'})
            return

    database.activate_key(license_key, id_number, receiver_email)
    
    # Send initial license status
    if key_data['duration_minutes'] == -1:
        socketio.emit('license_status', {'time_remaining': 'Lifetime'}, room=sid)

    script_path = os.path.join(os.path.dirname(__file__), 'script.py')
    
    try:
        process = subprocess.Popen(
            ['python', '-u', script_path, id_number, password, receiver_email],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        processes[license_key] = {'process': process, 'sid': sid}
        socketio.emit('terminal_output', {'data': '--- License validated. Script process started ---'}, room=sid)
        
        thread = threading.Thread(target=stream_output, args=(process, license_key))
        thread.daemon = True
        thread.start()

    except Exception as e:
        socketio.emit('terminal_output', {'data': f'--- Failed to start script: {e} ---'}, room=sid)

@socketio.on('stop_script')
def handle_stop_script(data):
    """Stops the running script associated with a license key."""
    license_key = data.get('license_key')
    sid = request.sid
    if license_key in processes and processes[license_key]['process'].poll() is None:
        try:
            processes[license_key]['process'].terminate()
            processes[license_key]['process'].wait(timeout=5)
            socketio.emit('terminal_output', {'data': '--- Script process terminated by user ---'}, room=sid)
            socketio.emit('script_finished', {'code': 'Terminated'}, room=sid)
        except subprocess.TimeoutExpired:
            processes[license_key]['process'].kill()
            socketio.emit('terminal_output', {'data': '--- Script process force-killed ---'}, room=sid)
            socketio.emit('script_finished', {'code': 'Killed'}, room=sid)
        # The stream_output thread will handle cleanup
    else:
        socketio.emit('terminal_output', {'data': '--- No script is currently running for this license ---'}, room=sid)

@socketio.on('check_status')
def handle_check_status(data):
    """Allows a returning user to check the status of their script."""
    license_key = data.get('license_key')
    sid = request.sid
    if license_key in processes and processes[license_key]['process'].poll() is None:
        processes[license_key]['sid'] = sid # Re-associate the new session ID
        socketio.emit('reconnect_success', {'status': 'running'}, room=sid)
        socketio.emit('terminal_output', {'data': '--- Reconnected to running script ---'}, room=sid)
    else:
        socketio.emit('reconnect_success', {'status': 'stopped'}, room=sid)

@socketio.on('disconnect')
def handle_disconnect():
    """Disassociates the session ID from a running process without stopping it."""
    sid = request.sid
    for key, data in processes.items():
        if data.get('sid') == sid:
            print(f"Client {sid} disconnected, but script for key {key} will continue running.")
            data['sid'] = None # Disassociate, don't kill
            break

if __name__ == '__main__':
    database.init_db()
    # Start the license manager as a background thread
    license_thread = threading.Thread(target=license_manager)
    license_thread.daemon = True
    license_thread.start()
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)

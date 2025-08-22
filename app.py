from flask import Flask, render_template, request, url_for, redirect, session, flash
from flask_socketio import SocketIO
import subprocess
import os
import threading
import time
import datetime
import database

app = Flask(__name__)
app.config['SECRET_KEY'] = 'a_very_secret_key_for_production'
app.config['ADMIN_PASSWORD'] = 'changethispassword' # IMPORTANT: Change this in production

# Use eventlet for non-blocking I/O, which is crucial for streaming
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="https://enlist.eces.ovh")

# Dictionary to hold session data (process and key), keyed by session ID
sessions = {}

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
                
                # Find the session associated with this key
                active_sid = None
                for sid, session_data in list(sessions.items()):
                    if session_data.get('key') == key_data['key']:
                        active_sid = sid
                        break
                
                if start_time + duration < datetime.datetime.now():
                    print(f"License key {key_data['key']} has expired. Terminating process.")
                    database.deactivate_key(key_data['key'])
                    if active_sid and sessions[active_sid]['process'].poll() is None:
                        sessions[active_sid]['process'].terminate()
                        socketio.emit('terminal_output', {'data': '--- LICENSE EXPIRED: Your time has run out. ---'}, room=active_sid)
                        socketio.emit('script_finished', {'code': 'Expired'}, room=active_sid)
                else:
                    # Send time remaining update
                    remaining = (start_time + duration) - datetime.datetime.now()
                    remaining_str = str(remaining).split('.')[0] # Format as H:MM:SS
                    if active_sid:
                        socketio.emit('license_status', {'time_remaining': remaining_str}, room=active_sid)

            time.sleep(1) # Check every second for a responsive timer

def stream_output(process, sid):
    """Reads output from the process and streams it to the client."""
    try:
        for line in iter(process.stdout.readline, ''):
            socketio.emit('terminal_output', {'data': line.strip()}, room=sid)
        process.stdout.close()
        return_code = process.wait()
        socketio.emit('script_finished', {'code': return_code}, room=sid)
    except Exception as e:
        print(f"Stream output thread error for SID {sid}: {e}")
    finally:
        # Clean up the session from the dictionary once the stream is done
        if sid in sessions:
            key = sessions[sid].get('key')
            if key:
                database.deactivate_key(key)
            del sessions[sid]

@app.route('/')
def index():
    """Serves the main HTML page."""
    return render_template('index.html')

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
    if sid in sessions and 'process' in sessions[sid] and sessions[sid]['process'].poll() is None:
        socketio.emit('terminal_output', {'data': '--- Your script is already running ---'}, room=sid)
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
    sessions[sid] = {'key': license_key} # Store key info with the session
    
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
        sessions[sid]['process'] = process # Add the process object to the session data
        socketio.emit('terminal_output', {'data': '--- License validated. Script process started ---'}, room=sid)
        
        thread = threading.Thread(target=stream_output, args=(process, sid))
        thread.daemon = True
        thread.start()

    except Exception as e:
        socketio.emit('terminal_output', {'data': f'--- Failed to start script: {e} ---'}, room=sid)

@socketio.on('stop_script')
def handle_stop_script():
    """Stops the running script for the specific user."""
    sid = request.sid
    if sid in sessions and 'process' in sessions[sid] and sessions[sid]['process'].poll() is None:
        try:
            database.deactivate_key(sessions[sid]['key'])
            sessions[sid]['process'].terminate()
            sessions[sid]['process'].wait(timeout=5)
            socketio.emit('terminal_output', {'data': '--- Script process terminated by user ---'}, room=sid)
            socketio.emit('script_finished', {'code': 'Terminated'}, room=sid)
        except subprocess.TimeoutExpired:
            sessions[sid]['process'].kill()
            socketio.emit('terminal_output', {'data': '--- Script process force-killed ---'}, room=sid)
            socketio.emit('script_finished', {'code': 'Killed'}, room=sid)
        del sessions[sid]
    else:
        socketio.emit('terminal_output', {'data': '--- You have no script running ---'}, room=sid)

@socketio.on('disconnect')
def handle_disconnect():
    """Cleans up the process when a user disconnects."""
    sid = request.sid
    if sid in sessions and 'process' in sessions[sid] and sessions[sid]['process'].poll() is None:
        print(f"Client {sid} disconnected. Terminating their script.")
        database.deactivate_key(sessions[sid]['key'])
        sessions[sid]['process'].terminate()
        try:
            sessions[sid]['process'].wait(timeout=5)
        except subprocess.TimeoutExpired:
            sessions[sid]['process'].kill()
        del sessions[sid]

if __name__ == '__main__':
    database.init_db()
    # Start the license manager as a background thread
    license_thread = threading.Thread(target=license_manager)
    license_thread.daemon = True
    license_thread.start()
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)

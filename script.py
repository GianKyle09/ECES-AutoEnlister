import os
import argparse
from helium import *
from helium._impl import sleep
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver import Chrome, ChromeOptions as Options
from selenium.webdriver.chrome.service import Service as ChromeService
from bs4 import BeautifulSoup
import smtplib, ssl
from email.message import EmailMessage
from datetime import datetime, timedelta

# --- Script Settings ---
PATIENCE = 30 # time in seconds to wait when site loads before timing out
ENROLL = True # set true if you're enrolling, false if you're sniping
TIMEOUT = 5 # time in seconds to wait after a check

# --- Email Notification Settings ---
SENDER_EMAIL = "eces@dlsu.edu.ph"
APP_PASSWORD = "qimg yzbo tofh sfan"
# RECEIVER_EMAIL is now passed as an argument
notification_cooldowns = {} # Stores the last notification time for each class

def send_notification_email(class_name, login_url, receiver_email):
    """Constructs and sends a beautiful HTML email notification with per-class cooldown."""
    global notification_cooldowns
    
    # Per-class cooldown check: 1 hour
    last_notified = notification_cooldowns.get(class_name)
    if last_notified and (datetime.now() - last_notified) < timedelta(hours=1):
        print(f"Notification for {class_name} already sent within the last hour. Skipping email.", flush=True)
        return

    subject = f"AnimoSys Class Open: {class_name}"
    
    html_body = f"""
    <html>
      <head>
        <style>
          body {{ font-family: Arial, sans-serif; margin: 0; padding: 0; background-color: #f4f4f4; }}
          .container {{ max-width: 600px; margin: 20px auto; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }}
          .header {{ background-color: #006A4E; color: #ffffff; padding: 20px; text-align: center; }}
          .header h1 {{ margin: 0; font-size: 24px; }}
          .content {{ padding: 30px; color: #333333; line-height: 1.6; }}
          .content p {{ margin: 0 0 15px; }}
          .class-info {{ background-color: #e8f5e9; padding: 15px; border-left: 5px solid #4CAF50; margin-bottom: 20px; border-radius: 4px; }}
          .class-info strong {{ font-size: 18px; color: #006A4E; }}
          .button-container {{ text-align: center; margin-top: 25px; }}
          .button {{ background-color: #007bff; color: #ffffff; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block; }}
          .footer {{ background-color: #f2f2f2; color: #777777; padding: 15px; text-align: center; font-size: 12px; }}
        </style>
      </head>
      <body>
        <div class="container">
          <div class="header">
            <h1>AnimoSys Class Alert</h1>
          </div>
          <div class="content">
            <p>Good news!</p>
            <div class="class-info">
              <p>An open slot was detected for the following class:</p>
              <strong>{class_name}</strong>
            </div>
            <p>The script is now attempting to enroll you automatically.</p>
            <div class="button-container">
              <a href="{login_url}" class="button">Go to AnimoSys</a>
            </div>
          </div>
          <div class="footer">
            <p>This is an automated notification from your AnimoSys Auto-Enlister script.</p>
          </div>
        </div>
      </body>
    </html>
    """
    
    em = EmailMessage()
    em['From'] = SENDER_EMAIL
    em['To'] = receiver_email
    em['Subject'] = subject
    em.set_content(f"An open slot was detected for {class_name}. The script is attempting to enroll you. Go to AnimoSys: {login_url}")
    em.add_alternative(html_body, subtype='html')

    context = ssl.create_default_context()

    try:
        print(f"Connecting to Gmail server to send notification for {class_name}...", flush=True)
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context) as smtp:
            smtp.login(SENDER_EMAIL, APP_PASSWORD)
            smtp.send_message(em)
        print("Notification email sent successfully!", flush=True)
        notification_cooldowns[class_name] = datetime.now() # Update cooldown for this specific class
    except Exception as e:
        print(f"Error: Could not send email. {e}", flush=True)

# --- Navigation ---
enrollment_url = "https://animo.sys.dlsu.edu.ph/psp/ps/EMPLOYEE/HRMS/s/WEBLIB_PTPP_SC.HOMEPAGE.FieldFormula.IScript_AppHP?pt_fname=HCCC_ENROLLMENT&FolderPath=PORTAL_ROOT_OBJECT.CO_EMPLOYEE_SELF_SERVICE.HCCC_ENROLLMENT&IsFolder=true"
url = "https://animo.sys.dlsu.edu.ph/psp/ps/"

def navigate_to_shopping_cart():
    """A reliable, reusable function to navigate to the shopping cart."""
    print("Navigating to the shopping cart page...", flush=True)
    try:
        go_to(enrollment_url)
        wait_until(Link("Enrollment: Add Classes").exists, timeout_secs=PATIENCE)
        click("Enrollment: Add Classes")
        # Final verification
        wait_until(Text("AY 2025-2026, Term 1 Shopping Cart").exists, timeout_secs=PATIENCE)
        print("Successfully navigated to shopping cart.", flush=True)
        return True
    except TimeoutException:
        print("Failed to navigate. Will retry on the next loop.", flush=True)
        sleep(TIMEOUT)
        return False

def main(id_number, password, receiver_email):
    """The main function to run the scraper."""
    # Configure Chrome options for a quieter, more stable headless operation
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox') # Essential for running as a non-root user like www-data
    options.add_argument('--disable-dev-shm-usage') # Overcomes resource limitations in a server environment
    options.add_argument(f'--user-data-dir=/tmp/selenium_{os.getpid()}') # Creates a unique data dir for each run
    options.add_argument('--disable-gpu')
    options.add_argument('--log-level=3')
    options.add_experimental_option('excludeSwitches', ['enable-logging'])

    
    # Explicitly point to the system's installed chromedriver
    service = ChromeService(executable_path='/var/www/home/.cache/selenium/chromedriver/linux64/139.0.7258.138/chromedriver')
    driver = Chrome(service=service, options=options)
    set_driver(driver)
    go_to(url)


    # login into Animosys
    print("Logging into AnimoSys...", flush=True)
    wait_until(Button('Sign In').exists, timeout_secs=PATIENCE)
    write(id_number, into="User ID:")
    write(password, into="Password:")
    click("Sign In")
    print("Login successful.", flush=True)

    wait_until(Link("Self Service").exists, timeout_secs=PATIENCE)

    # --- Initial Navigation ---
    navigate_to_shopping_cart()

    # --- Main Class Sniper Loop (BeautifulSoup Implementation) ---
    while True:
        try:
            # 1. Verify we are on the correct page.
            wait_until(Text("AY 2025-2026, Term 1 Shopping Cart").exists, timeout_secs=15)
            print(f"Checking for open classes... (Last check: {datetime.now().strftime('%I:%M:%S %p')})", flush=True)

            # 2. Get a static snapshot of the page's HTML for stable parsing
            soup = BeautifulSoup(get_driver().page_source, 'html.parser')
            
            # 3. Find all rows in the main shopping cart table
            cart_table = soup.find('table', id='SSR_REGFORM_VW$scroll$0')
            if not cart_table:
                print("Could not find the main shopping cart table. Refreshing...", flush=True)
                navigate_to_shopping_cart()
                continue

            rows = cart_table.find_all('tr', id=lambda x: x and x.startswith('trSSR_REGFORM_VW$0_'))
            open_class_found = False

            if not rows:
                print("No class rows found in cart table. Refreshing...", flush=True)
                navigate_to_shopping_cart()
                continue

            # 4. Analyze each row for class name and status
            for row in rows:
                open_image = row.find('img', alt='Open')
                if open_image:
                    class_link = row.find('a', id=lambda x: x and x.startswith('P_CLASS_NAME$'))
                    if class_link:
                        class_name = class_link.get_text(strip=True).replace('\n', ' ')
                        print(f"--- CLASS OPEN DETECTED: {class_name} ---", flush=True)
                        send_notification_email(class_name, url, receiver_email)
                        open_class_found = True
            
            # 5. Act based on the results of the full scan
            if open_class_found and ENROLL:
                print("Proceeding with enrollment...", flush=True)
                wait_until(Link("Proceed to Step 2 of 3").exists, timeout_secs=PATIENCE)
                click("Proceed to Step 2 of 3")
                wait_until(Link("Finish Enrolling").exists, timeout_secs=PATIENCE)
                click("Finish Enrolling")
                wait_until(Link("Add Another Class").exists, timeout_secs=PATIENCE)
                print("Enrollment attempt finished. Returning to shopping cart...", flush=True)
                click("Add Another Class")
            elif open_class_found:
                print("ENROLL is set to False. Not attempting enrollment. Refreshing...", flush=True)
                navigate_to_shopping_cart()
            else:
                print("No open classes found. Refreshing...", flush=True)
                navigate_to_shopping_cart()

        except TimeoutException:
            print("A timeout occurred. Resetting navigation...", flush=True)
            navigate_to_shopping_cart()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='AnimoSys Auto-Enlister')
    parser.add_argument('id_number', type=str, help='Your AnimoSys ID number')
    parser.add_argument('password', type=str, help='Your AnimoSys password')
    parser.add_argument('receiver_email', type=str, help='Email address for notifications')
    args = parser.parse_args()
    
    main(args.id_number, args.password, args.receiver_email)


RESPECT QUEST - BEGINNER SETUP
==============================

1. Install Python 3.11+.

2. Open this folder in VS Code.

3. Open VS Code Terminal.

4. Create a virtual environment:

   python -m venv venv

5. Activate it on Windows:

   venv\Scripts\activate

6. Install packages:

   pip install -r requirements.txt

7. Put the question workbook in this project folder. The importer uses the
   first .xlsx workbook it finds.

8. IMPORTANT:
   The importer expects sheets named:
      Level_1
      Level_2
      ...
      Level_10
   It also requires an Option_Ratings sheet with columns:
      Level, Question No, Option A Rating, Option B Rating,
      Option C Rating, Option D Rating

   Rate each option from 1 to 5 stars. The correct option must be rated 5;
   incorrect options use 1 (unrelated or contradictory), 2 (related but
   clearly wrong), 3 (partly relevant with a major flaw), or 4 (nearly
   correct). Existing ratings are in the project's question workbook.

   Your workbook should have columns such as:
      Q.No
      Question
      Option A
      Option B
      Option C
      Option D
      Correct Answer
      Explanation / Learning Point
      Learning Objective
      Difficulty

9. Import questions:

   python import_questions.py

10. Start the website:

   python app.py

11. Open:

   http://127.0.0.1:5000

DEMO ADMIN
----------
Username: admin
Password: admin123

Change the secret key and admin password before real deployment.

PASSWORD RECOVERY
-----------------
New accounts require a mobile number in international format (for example,
+919876543210).

To enable Gmail OTP delivery, set these environment variables before starting:
   SMTP_USERNAME=your-gmail-address
   SMTP_PASSWORD=your-gmail-app-password
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=465

For easier local setup, copy `.env.example` to `.env`, replace the values,
then restart `python app.py`. The application loads `.env` automatically.
Do not put real credentials in `.env.example` or share your `.env` file.

If the terminal prints "Gmail OTP is disabled", the `.env` file is missing,
has empty values, or Flask was not restarted after editing it. The registered
email must be entered in the recovery form, for example:
   deepsthiru4071@gmail.com

Use a Gmail App Password; do not use your regular Gmail password. To enable
SMS OTP delivery through Twilio, set:
   TWILIO_ACCOUNT_SID=your-account-sid
   TWILIO_AUTH_TOKEN=your-auth-token
   TWILIO_FROM_NUMBER=your-twilio-number

OTP codes expire after 10 minutes and are limited to five verification
attempts. Existing accounts can use email recovery immediately; they need a
mobile number added to the database before SMS recovery is available.

IMPORTANT
---------
The project is currently configured with your 10 question-number selections
for each level. Do not invent missing question content; add your actual
questions to the Excel workbook.

The uploaded Level 2 workbook can be imported if it is named as a Level 2
Questions sheet, but Levels 1 and 3-10 still need their actual question
content before those levels can be played.

import sqlite3
import openpyxl
from pathlib import Path
from app import DB_PATH, init_db

# ---------------- LEVEL QUESTION SELECTION ---------------- #

LEVEL_MAP = {
    1: [2, 4, 5, 6, 8, 11, 12, 16, 18, 20],
    2: [1, 3, 5, 6, 8, 12, 13, 17, 19, 20],
    3: [2, 5, 8, 10, 11, 13, 18, 19, 20, 15],
    4: [1, 3, 4, 6, 7, 9, 10, 12, 14, 15],
    5: [1, 4, 6, 9, 12, 13, 14, 15, 16, 20],
    6: [2, 5, 7, 9, 11, 15, 17, 18, 19, 20],
    7: [3, 5, 7, 9, 11, 13, 15, 17, 19, 20],
    8: [2, 4, 6, 8, 10, 12, 14, 16, 18, 20],
    9: [1, 4, 6, 8, 9, 11, 13, 15, 17, 19],
    10: [3, 5, 7, 9, 10, 11, 12, 14, 16, 17]
}

# --------------------------------------------------------- #

# Automatically find the Excel file
excel_files = list(Path(".").glob("*.xlsx"))

if not excel_files:
    print("❌ No Excel file found in this folder.")
    exit()

excel_file = excel_files[0]
print("📄 Reading:", excel_file.name)

wb = openpyxl.load_workbook(excel_file, data_only=True)
if "Option_Ratings" not in wb.sheetnames:
    raise ValueError(
        "The workbook is missing the Option_Ratings sheet. Add a 1–5 rating "
        "for each answer option before importing questions."
    )

ratings_ws = wb["Option_Ratings"]
option_ratings = {}
for row in ratings_ws.iter_rows(min_row=2, values_only=True):
    if row[0] is None or row[1] is None:
        continue
    level, q_no = int(row[0]), int(row[1])
    ratings = [int(value) for value in row[2:6] if value is not None]
    if len(ratings) != 4 or any(rating not in range(1, 6) for rating in ratings):
        raise ValueError(
            f"Ratings for Level {level}, Question {q_no} must be from 1 to 5."
        )
    option_ratings[(level, q_no)] = ratings

# Validate the whole import before replacing the existing question bank.
for level, selected_questions in LEVEL_MAP.items():
    sheet_name = f"Level_{level}"
    if sheet_name not in wb.sheetnames:
        raise ValueError(f"The workbook is missing the {sheet_name} sheet.")
    ws = wb[sheet_name]
    found_questions = set()
    for row in ws.iter_rows(min_row=4, values_only=True):
        if row[0] is None:
            continue
        q_no = int(row[0])
        if q_no not in selected_questions:
            continue
        found_questions.add(q_no)
        correct_answer = str(row[7]).strip().upper()
        ratings = option_ratings.get((level, q_no))
        if correct_answer not in {"A", "B", "C", "D"} or not ratings:
            raise ValueError(
                f"Missing a valid correct answer or option ratings for "
                f"Level {level}, Question {q_no}."
            )
        if ratings[ord(correct_answer) - ord("A")] != 5:
            raise ValueError(
                f"The correct option must have 5 stars for Level {level}, "
                f"Question {q_no}."
            )
        if any(
            rating == 5
            for index, rating in enumerate(ratings)
            if index != ord(correct_answer) - ord("A")
        ):
            raise ValueError(
                f"Only the correct option can have 5 stars for Level {level}, "
                f"Question {q_no}."
            )
    missing_questions = set(selected_questions) - found_questions
    if missing_questions:
        raise ValueError(
            f"{sheet_name} is missing selected questions: "
            f"{sorted(missing_questions)}."
        )
    missing_ratings = set(selected_questions) - {
        q_no for rating_level, q_no in option_ratings
        if rating_level == level
    }
    if missing_ratings:
        raise ValueError(
            f"Missing option ratings for Level {level}: "
            f"{sorted(missing_ratings)}."
        )

# Replace the question bank only after every row and rating passes validation.
init_db()
conn = sqlite3.connect(DB_PATH)
conn.execute("DELETE FROM questions")

total_questions = 0

# --------------------------------------------------------- #
# IMPORT QUESTIONS
# --------------------------------------------------------- #

for level in range(1, 11):

    sheet_name = f"Level_{level}"

    if sheet_name not in wb.sheetnames:
        print(f"⚠ Sheet {sheet_name} not found.")
        continue

    ws = wb[sheet_name]

    print(f"\n📘 Importing Level {level}...")

    for row in ws.iter_rows(min_row=4, values_only=True):

        if row[0] is None:
            continue

        q_no = int(row[0])

        # Import only selected questions for this level
        if q_no not in LEVEL_MAP[level]:
            continue

        question = row[2]
        option_a = row[3]
        option_b = row[4]
        option_c = row[5]
        option_d = row[6]
        correct_answer = row[7]
        ratings = option_ratings.get((level, q_no))
        if ratings is None:
            raise ValueError(
                f"Missing option ratings for Level {level}, Question {q_no}."
            )

        # The workbook stores the BBBP feedback in its final two columns.
        positive_message = row[16] if len(row) > 16 and row[16] else ""
        learning_message = row[17] if len(row) > 17 and row[17] else ""
        explanation = learning_message
        learning_objective = ""
        difficulty = "Medium"

        # ---------- UNIQUE BBBP FEEDBACK ---------- #

        positive_message = positive_message or explanation
        learning_message = learning_message or explanation

        # ---------- SAVE TO DATABASE ---------- #

        conn.execute("""
        INSERT INTO questions(
            level,
            question,
            option_a,
            option_b,
            option_c,
            option_d,
            correct_answer,
            option_a_rating,
            option_b_rating,
            option_c_rating,
            option_d_rating,
            explanation,
            learning_objective,
            difficulty,
            positive_message,
            learning_message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            level,
            question,
            option_a,
            option_b,
            option_c,
            option_d,
            correct_answer,
            *ratings,
            explanation,
            learning_objective,
            difficulty,
            positive_message,
            learning_message
        ))

        total_questions += 1

    print(f"   ✅ Level {level}: 10 questions imported.")

conn.commit()
conn.close()

print("\n======================================")
print(f"🎮 Successfully imported {total_questions} questions.")
print("🌸 Respect Quest database is ready!")
print("======================================")
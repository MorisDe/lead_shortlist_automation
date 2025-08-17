from flask import Flask, request, jsonify, send_file
import os
import logging
import time
from functools import wraps
from dotenv import load_dotenv
from pyairtable import Api
import json
import re
from datetime import datetime
from currency_converter import CurrencyConverter
from langchain_groq import ChatGroq
from langchain_core.prompts import PromptTemplate
from flask_cors import CORS


app = Flask(__name__)
CORS(app)
load_dotenv()


logging.basicConfig(
    filename='employee_shortlist.log',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


employee_logger = logging.getLogger("employee_shortlist")
employee_logger.setLevel(logging.ERROR)

fh = logging.FileHandler("employee_shortlist.log")
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
fh.setFormatter(formatter)
employee_logger.addHandler(fh)


def retry_on_failure(max_retries=3, delay=2):
    """Retry decorator for Airtable operations"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    employee_logger.error(
                        f"Attempt {attempt} failed for {func.__name__}: {e}",
                        exc_info=True
                    )
                    if attempt < max_retries:
                        time.sleep(delay)
                    else:
                        employee_logger.error(
                            f"All {max_retries} retries failed for {func.__name__}"
                        )
                        raise
        return wrapper
    return decorator



api = Api(os.getenv('airtable_api'))
base_id = os.getenv('base_id')

applicant_id_conn = api.table(base_id, 'Applicants')
personal_detail_conn = api.table(base_id, 'Personal Details')
work_experience_conn = api.table(base_id, 'Work Experience')
salary_preferences_conn = api.table(base_id, 'Salary Preferences')



@retry_on_failure()
def safe_create_applicant(data):
    return applicant_id_conn.create(data)

@retry_on_failure()
def safe_update_applicant(record_id, fields):
    return applicant_id_conn.update(record_id, fields)

@retry_on_failure()
def safe_batch_update(records):
    return applicant_id_conn.batch_update(records)

@retry_on_failure()
def safe_get(table_conn, record_id):
    return table_conn.get(record_id)



def make_counter():
    count = 0
    def counter():
        nonlocal count
        count += 1
        return count
    return counter

new_id = make_counter()


def build_combined_json(applicant):
    try:
        applicant_fields = applicant['fields']
        applicant_id = applicant_fields.get('Applicant ID')

        # --- Personal details ---
        personal = {}
        personal_ids = applicant_fields.get("Personal Details", [])
        if personal_ids:
            personal_record = safe_get(personal_detail_conn, personal_ids[0])
            personal = personal_record['fields']

        # --- Work experience ---
        experiences = []
        work_ids = applicant_fields.get("Work Experience", [])
        for wid in work_ids:
            work_record = safe_get(work_experience_conn, wid)
            work_fields = work_record['fields']
            experiences.append({
                "company": work_fields.get("Company"),
                "title": work_fields.get("Title"),
                "start": work_fields.get("Start"),
                "end": work_fields.get("End"),
                "technologies": work_fields.get("Technologies")
            })

        # --- Salary preferences ---
        salary = {}
        salary_ids = applicant_fields.get("Salary Preferences", [])
        if salary_ids:
            salary_record = safe_get(salary_preferences_conn, salary_ids[0])
            salary = salary_record['fields']

        return {
            "applicant_id": applicant_id,
            "personal": {
                "name": personal.get("Full Name"),
                "location": personal.get("Location")
            },
            "experience": experiences,
            "salary": {
                "rate": salary.get("Preferred Rate"),
                "currency": salary.get("Currency"),
                "availability": salary.get("Availability (hrs/wk)"),
            }
        }
    except Exception as e:
        employee_logger.error(f"Error building combined JSON: {e}", exc_info=True)
        return {}


def push_combined_json():
    try:
        all_applicants = applicant_id_conn.all()
        for applicant in all_applicants:
            combined_json = build_combined_json(applicant)
            if not combined_json:
                continue
            compressed_str = json.dumps(combined_json, separators=(',', ':'))
            safe_batch_update([{
                "id": applicant["id"],
                "fields": {"Compressed JSON": compressed_str}
            }])
    except Exception as e:
        employee_logger.error(f"Error pushing combined JSON: {e}", exc_info=True)


def calculate_experience(experiences):
    total_days = 0
    for exp in experiences:
        try:
            start = datetime.strptime(exp["start"], "%Y-%m-%d")
            end = datetime.strptime(exp["end"], "%Y-%m-%d")
            total_days += (end - start).days
        except Exception as e:
            employee_logger.error(f"Error calculating experience: {e}", exc_info=True)
    return round(total_days / 365, 2)


def extract_applicant_summaries(applicants):
    summaries = []
    for record in applicants:
        try:
            compressed_data = json.loads(record["fields"]["Compressed JSON"])
            companies = [exp["company"] for exp in compressed_data["experience"]]
            summaries.append({
                "id": record["id"],
                "name": compressed_data["personal"]["name"],
                "location": compressed_data["personal"]["location"],
                "total_experience_years": calculate_experience(compressed_data["experience"]),
                "preferred_rate": f"{compressed_data['salary']['rate']} {compressed_data['salary']['currency']}",
                "availability": compressed_data["salary"]["availability"],
                "companies": companies
            })
        except Exception as e:
            employee_logger.error(f"Error extracting applicant summary: {e}", exc_info=True)
    return summaries


def currencyconverter(value):
    try:
        c = CurrencyConverter()
        if "Others" in str(value):
            return False
        amount = re.search(r"\d+(?:\.\d+)?", str(value))
        amount = float(amount.group()) if amount else None
        currency = re.search(r"\b[A-Z]{3}\b", str(value))
        currency = currency.group() if currency else None
        if not amount or not currency:
            return False
        return c.convert(amount, currency, 'USD')
    except Exception as e:
        employee_logger.error(f"Error converting currency: {e}", exc_info=True)
        return False


def shortlist_candidates(cleaned_data):
    allowed_locations = {"united states", "united kingdom", "canada", "germany", "india"}
    tier_one_companies = ['google', 'meta', 'microsoft', 'nvidia']
    shortlisted = []
    for i in cleaned_data:
        try:
            in_allowed_location = i["location"].lower() in allowed_locations
            in_tier_one_company = any(c.lower() in tier_one_companies for c in i["companies"])
            compensation = currencyconverter(i['preferred_rate'])
            hours_available = i['availability'] >= 20
            if in_allowed_location and (i["total_experience_years"] >= 4 or in_tier_one_company) and (compensation and compensation <= 100 and hours_available):
                shortlisted.append(i)
        except Exception as e:
            employee_logger.error(f"Error shortlisting candidate {i.get('id')}: {e}", exc_info=True)
    return shortlisted



chat_model = ChatGroq(
    groq_api_key=os.getenv("groq_api_key"),
    model_name="llama-3.3-70b-versatile",
    temperature=0.2,
    max_retries=3
)

prompt_email = PromptTemplate.from_template(
    """
    You are a recruiting analyst. Given this {a} JSON applicant profile, do four things:
    1. Provide a concise 75-word summary.
    2. Rate overall candidate quality from 1-10 (higher is better).
    3. List any data gaps or inconsistencies you notice.
    4. Suggest up to three follow-up questions to clarify gaps.

    Note:
    The avalibility is hrs/week and the company is previous company the employee has worked in.

    Return exactly:
    Summary: <text>
    Score: <integer>
    Issues: <comma-separated list or 'None'>
    Follow-Ups: <bullet list>
    """
)

chain_email = prompt_email | chat_model


def parse_result(content: str):
    pattern = r"(Summary|Score|Issues|Follow-Ups):([\s\S]*?)(?=(Summary|Score|Issues|Follow-Ups|$))"
    sections = {match[0]: match[1].strip() for match in re.findall(pattern, content)}
    if "Issues" in sections:
        sections["Issues"] = [i.strip() for i in sections["Issues"].split(",")]
    if "Follow-Ups" in sections:
        sections["Follow-Ups"] = [
            f.strip("* ").strip() for f in sections["Follow-Ups"].split("\n") if f.strip()
        ]
    return sections


def enrich_with_llm(shortlisted_candidates):
    airtable_records = []
    for candidate in shortlisted_candidates:
        try:
            result = chain_email.invoke({"a": str(candidate)})
            llm_response = parse_result(result.content)
            record = {
                "id": candidate["id"],
                "fields": {
                    "LLM Summary": llm_response.get("Summary", "NA"),
                    "LLM Score": int(llm_response.get("Score", 0)),
                    "LLM Follow-Ups": "; ".join(llm_response.get("Follow-Ups", [])) or "NA",
                    "Shortlist Status": "Yes" if int(llm_response.get("Score", 0)) > 1 else "No"
                }
            }
            safe_update_applicant(record["id"], record["fields"])
            airtable_records.append(record)
        except Exception as e:
            employee_logger.error(f"Error enriching candidate {candidate.get('id')}: {e}", exc_info=True)
    return airtable_records


@app.route('/')
def index():
    return send_file('index.html')


@app.route('/formdata', methods=['POST'])
def create_applicant():
    try:
        # Step 1: Gather inputs
        full_name = request.form.get('full_name')
        email = request.form.get('email')
        location = request.form.get('location')
        linkedin = request.form.get('linkedin')
        logging.info(f"Received form submission from {full_name} ({email})")

        # Step 2: Collect work experiences
        work_experiences = []
        i = 0
        while f'company_{i}' in request.form:
            work_experiences.append({
                'company': request.form.get(f'company_{i}'),
                'title': request.form.get(f'title_{i}'),
                'start_date': request.form.get(f'start_{i}'),
                'end_date': request.form.get(f'end_{i}'),
                'technologies': request.form.get(f'technologies_{i}')
            })
            i += 1

        preferred_rate = request.form.get('preferred_rate', type=float)
        minimum_rate = request.form.get('minimum_rate', type=float)
        currency = request.form.get('currency')
        availability = request.form.get('availability', type=int)

        # Step 3: Create applicant
        new_applicant = safe_create_applicant({'Applicant ID': new_id()})
        applicant_id = new_applicant['id']

        # Step 4: Create personal details
        personal_detail_conn.create({
            "Full Name": full_name,
            "Email": email,
            "Location": location,
            "LinkedIn": linkedin,
            "Applicants": [applicant_id]
        })

        # Step 5: Work experiences
        for exp in work_experiences:
            work_experience_conn.create({
                "Company": exp['company'],
                "Title": exp['title'],
                "Start": exp['start_date'],
                "End": exp['end_date'],
                "Technologies": exp['technologies'],
                "Applicant ID": [applicant_id]
            })

        # Step 6: Salary preferences
        salary_preferences_conn.create({
            "Preferred Rate": preferred_rate,
            "Minimum Rate": minimum_rate,
            "Currency": currency,
            "Availability (hrs/wk)": availability,
            "Applicants": [applicant_id]
        })

        # Step 7: Update compressed JSON
        push_combined_json()

        # Step 8: Extract all applicants
        cleaned_data = extract_applicant_summaries(
            applicant_id_conn.all(sort=["Compressed JSON"])
        )

        # Step 9: Shortlist
        shortlisted = shortlist_candidates(cleaned_data)

        # Step 10: Update non-shortlisted
        shortlisted_ids = {c["id"] for c in shortlisted}
        updates = [{
            "id": record["id"],
            "fields": {
                "LLM Summary": "NA",
                "LLM Score": 0,
                "LLM Follow-Ups": "NA",
                "Shortlist Status": "No"
            }
        } for record in cleaned_data if record["id"] not in shortlisted_ids]
        for batch_start in range(0, len(updates), 10):
            safe_batch_update(updates[batch_start:batch_start + 10])

        # Step 11: LLM enrichment
        enriched_records = enrich_with_llm(shortlisted)

        applicant_number = new_applicant['fields']['Applicant ID']
        logging.info(f"Applicant {full_name} ({applicant_number}) created successfully.")
        logging.info(f"Shortlisting complete. {len(shortlisted)} candidates shortlisted.")

        return jsonify({
            "message": "Applicant created and processed successfully!",
            "new_id": applicant_number,
            "shortlisted": shortlisted,
            "llm_records": enriched_records
        })

    except Exception as e:
        logging.error(f"Error creating applicant: {e}", exc_info=True)
        return jsonify({"error": "An error occurred while creating the applicant."}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)

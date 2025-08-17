Lead Applicant Shortlist Automation Documentation
1. Setup Steps
Prerequisites
Python 3.9+


Airtable account with API key


Groq API key for LLM integration


Installation
Clone the repository or copy the Flask app script.
Create a virtual environment and install dependencies: pip install -r requirements.txt
Create a .env file in the project root with the following keys:
 airtable_api=YOUR_AIRTABLE_API_KEY
base_id=YOUR_AIRTABLE_BASE_ID
groq_api_key=YOUR_GROQ_API_KEY
Run the Flask app: python app.py
Access the application at http://localhost:5000.


2. Airtable Schema and Field Definitions
The app expects the following Airtable tables:
Applicants
Applicant ID (auto-increment integer, unique identifier)


Compressed JSON (long text, generated automation output)


LLM Summary (text, added after LLM enrichment)


LLM Score (number, 0–10 scale)


LLM Follow-Ups (long text, follow-up questions)


Shortlist Status (single select: Yes/No)


Personal Details
Full Name (text)


Email (email)


Location (text)


LinkedIn (URL)


Applicants (linked to Applicants table)


Work Experience
Company (text)


Title (text)


Start (date)


End (date)


Technologies (text/multi-select)


Applicant ID (linked to Applicants table)


Salary Preferences
Preferred Rate (number)


Minimum Rate (number)


Currency (3-letter ISO code)


Availability (hrs/wk) (number)


Applicants (linked to Applicants table)


3. Automations and Processing Logic
3.1 JSON Compression
Each applicant’s linked records (personal details, work experience, salary) are combined into a compressed JSON stored in the Compressed JSON field.
Snippet:
combined_json = build_combined_json(applicant)
compressed_str = json.dumps(combined_json, separators=(',', ':'))
safe_batch_update([{
    "id": applicant["id"],
    "fields": {"Compressed JSON": compressed_str}
}])

3.2 Candidate Summarization
The script calculates:
Total years of experience


Previous companies


Location


Salary preferences


Snippet:
summaries = extract_applicant_summaries(applicant_id_conn.all())

3.3 Shortlisting
Shortlisting applies filters:
Location is in the allowed set


At least 4 years of experience or Tier-1 company background


Rate ≤ 100 USD/hour


Availability ≥ 20 hours/week


Snippet:
if in_allowed_location and (years >= 4 or in_tier_one_company) and (compensation and compensation <= 100 and hours_available):
    shortlisted.append(i)

3.4 LLM Enrichment
Shortlisted candidates are sent to the Groq-hosted LLM. The model generates:
A 75-word summary


A quality score (1–10)


Issues/gaps


Follow-up questions


Snippet:
result = chain_email.invoke({"a": str(candidate)})
llm_response = parse_result(result.content)
safe_update_applicant(candidate["id"], {
    "LLM Summary": llm_response["Summary"],
    "LLM Score": int(llm_response["Score"]),
    "LLM Follow-Ups": "; ".join(llm_response["Follow-Ups"]),
    "Shortlist Status": "Yes"
})

4. LLM Integration and Security
Configuration
The application utilizes LangChain-Groq with the Llama-3.3-70b-Versatile model.
chat_model = ChatGroq(
    groq_api_key=os.getenv("groq_api_key"),
    model_name="llama-3.3-70b-versatile",
    temperature=0.2,
    max_retries=3
)

Security
The Groq API key is stored securely in .env.


Never hardcode API keys into the source code.


Restrict .env file access to authorized users only.


5. Extending Shortlist Criteria
To customize the filtering logic, edit the shortlist_candidates function:
Current criteria:
Location in {"united states", "united kingdom", "canada", "germany", "india"}


At least 4 years of experience OR Tier-1 company


Compensation ≤ 100 USD/hour


Availability ≥ 20 hours/week


Example: Adding Technology Filter
required_tech = {"python", "flask", "aws"}
has_required_tech = any(
    required in (exp["technologies"] or "").lower()
    for exp in i["experience"]
    for required in required_tech
)

if in_allowed_location and has_required_tech and (years >= 4 or in_tier_one_company):
    shortlisted.append(i)


Example: Changing Rate Limit
To increase the max acceptable rate:
if compensation and compensation <= 150:
    # proceed


6. Logging and Error Handling
model_form_submission.log: captures info-level logs for form submissions and overall processing.


employee_shortlist.log: captures only errors from Airtable operations and automation failures.


Retries:
Airtable calls (create, update, batch update, get) are wrapped with a retry decorator.


Up to 3 retries with a 2-second delay are attempted before failing.
7. Viewing Forms
Viewing all the forms can be done by running the python script and opening the index.html in the template folder




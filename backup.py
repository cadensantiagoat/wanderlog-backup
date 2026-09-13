import os
import requests
import json
import re
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Load variables from the .env file
load_dotenv()

WANDERLOG_AUTH = os.getenv("WANDERLOG_AUTH")
TRIP_ID = os.getenv("WANDERLOG_TRIP_ID")
DOC_ID = os.getenv("GOOGLE_DOC_ID")

# The path to your Google Service account JSON file
GOOGLE_CREDENTIALS_FILE = 'credentials.json'

def fetch_wanderlog_data():
    """Fetches the Wanderlog HTML page and extracts the MobX state JSON."""
    print(f"Fetching HTML for trip: {TRIP_ID}...")
    
    url = f"https://wanderlog.com/plan/{TRIP_ID}"
    print(f"Fetching HTML from: {url}")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Cookie": f"connect.sid={WANDERLOG_AUTH}"
    }

    response = requests.get(url, headers=headers)
    
    if response.status_code != 200:
        raise Exception(f"Failed to fetch page: {response.status_code}")
        
    print("Searching for window.__MOBX_STATE__ in the HTML...")
    
    # Regex to extract the JSON object assigned to the variable
    pattern = re.compile(r"window\.__MOBX_STATE__\s*=\s*(\{.*?\});", re.DOTALL)
    match = pattern.search(response.text)
    
    if not match:
        # Fallback regex in case there's no trailing semicolon
        pattern_fallback = re.compile(r"window\.__MOBX_STATE__\s*=\s*(\{.*?\})\s*</script>", re.DOTALL)
        match = pattern_fallback.search(response.text)
        
    if not match:
        raise Exception("Could not locate window.__MOBX_STATE__ in the HTML.")
        
    json_string = match.group(1)
    
    print("Successfully extracted data! Parsing JSON...")
    raw_json_data = json.loads(json_string)
    
    return raw_json_data

import json
import re

def extract_text_from_ops(text_obj):
    """Helper to extract clean text from Wanderlog/Quill rich-text ops."""
    if not isinstance(text_obj, dict):
        return ""
    ops = text_obj.get('ops')
    if not isinstance(ops, list):
        return ""
    
    text_pieces = []
    for op in ops:
        if isinstance(op, dict):
            insert_val = op.get('insert')
            if isinstance(insert_val, str):
                text_pieces.append(insert_val)
    return "".join(text_pieces)


def format_trip_data(trip_json):
    """Parses Wanderlog JSON and formats it into indented lists and text blocks."""
    print("Formatting trip data into readable lists...")
    
    document_text = "WANDERLOG TRIP BACKUP\n=====================\n\n"
    
    # 1. Helper function to recursively find all sections in the JSON tree
    def find_sections(data):
        sections = []
        if isinstance(data, dict):
            # A section has a 'heading' and either 'blocks' or 'text'
            if 'heading' in data and ('blocks' in data or 'text' in data):
                sections.append(data)
            for key, value in data.items():
                sections.extend(find_sections(value))
        elif isinstance(data, list):
            for item in data:
                sections.extend(find_sections(item))
        return sections

    all_lists = find_sections(trip_json)
    processed_headings = set()
    
    for section in all_lists:
        heading = section.get('heading', '').strip()
        if not heading:
            continue
            
        section_body = ""
        
        # A. Check for section-level text (e.g. standalone Notes section)
        section_text_obj = section.get('text')
        notes_text = extract_text_from_ops(section_text_obj)
        if notes_text.strip():
            section_body += notes_text.strip() + "\n\n"
            
        # B. Check for places/blocks inside the section
        blocks = section.get('blocks', [])
        place_index = 1
        
        for block in blocks:
            if block.get('type') == 'place':
                place_info = block.get('place', {})
                place_name = place_info.get('name', 'Unknown Location')
                
                section_body += f"{place_index}. {place_name}\n"
                
                # Address
                address = place_info.get('formatted_address') or place_info.get('address')
                if address:
                    section_body += f"\tAddress: {address}\n"
                    
                # Place-specific notes
                place_note = extract_text_from_ops(block.get('text'))
                fallback_note = block.get('description') or block.get('note') or place_info.get('userNote')
                
                if place_note.strip():
                    section_body += f"\tNotes: {place_note.strip()}\n"
                elif isinstance(fallback_note, str) and fallback_note.strip():
                    section_body += f"\tNotes: {fallback_note.strip()}\n"
                    
                place_index += 1
                
            elif block.get('type') == 'text' or 'text' in block:
                block_text = extract_text_from_ops(block.get('text'))
                if block_text.strip():
                    section_body += f"{block_text.strip()}\n\n"

        # ONLY add section to document if actual content was found
        if section_body.strip():
            heading_key = heading.upper()
            
            # Print diagnostic info to terminal
            print(f" -> Capturing section: '{heading}' ({len(section_body.strip())} chars)")
            
            document_text += f"{heading_key}\n"
            document_text += "-" * len(heading) + "\n"
            document_text += section_body.strip() + "\n\n\n"
            processed_headings.add(heading_key)
            
    # Sanitize ONLY harmful ASCII control characters (keeps normal letters, unicode, and line breaks)
    safe_string = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', document_text)
    
    return safe_string

def update_google_doc(text_content):
    """Clears the existing Google Doc and writes the new text content."""
    print("Authenticating with Google...")
    
    scopes = ['https://www.googleapis.com/auth/documents']
    
    # Check if credentials are passed via environment variable (GitHub Actions)
    env_creds = os.getenv("GOOGLE_CREDENTIALS_JSON")
    
    if env_creds:
        try:
            creds_dict = json.loads(env_creds)
            creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=scopes)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse GOOGLE_CREDENTIALS_JSON environment variable. Check your GitHub Secret content. Error: {e}")
    elif os.path.exists(GOOGLE_CREDENTIALS_FILE):
        # Fallback to local file for testing on your machine
        creds = service_account.Credentials.from_service_account_file(GOOGLE_CREDENTIALS_FILE, scopes=scopes)
    else:
        raise FileNotFoundError("No Google credentials found in environment variables or credentials.json file.")

    service = build('docs', 'v1', credentials=creds)
    
    print(f"Accessing Google Doc: {DOC_ID}...")
    
    doc = service.documents().get(documentId=DOC_ID).execute()
    old_end_index = doc.get('body').get('content')[-1].get('endIndex')
    
    if old_end_index > 2:
        print("Clearing old backup data...")
        try:
            service.documents().batchUpdate(
                documentId=DOC_ID, 
                body={'requests': [{
                    'deleteContentRange': {
                        'range': {
                            'startIndex': 1,
                            'endIndex': old_end_index - 1
                        }
                    }
                }]}
            ).execute()
        except Exception as e:
            print("Note: Could not clear old content completely. Writing new data at the top instead.")
            
    print("Writing new data to Google Doc...")
    service.documents().batchUpdate(
        documentId=DOC_ID, 
        body={'requests': [{
            'insertText': {
                'location': {'index': 1},
                'text': text_content
            }
        }]}
    ).execute()
    
    print("Backup successful!")

# This is the block that actually tells Python to run the functions!
if __name__ == "__main__":
    try:
        trip_data = fetch_wanderlog_data()
        formatted_text = format_trip_data(trip_data)
        update_google_doc(formatted_text)
    except Exception as e:
        print(f"An error occurred: {e}")
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

def format_trip_data(trip_json):
    """Parses Wanderlog JSON and formats it into indented lists and text blocks."""
    print("Formatting trip data into readable lists...")
    
    document_text = "WANDERLOG TRIP BACKUP\n=====================\n\n"
    
    # 1. Helper function to recursively find all lists in the raw JSON
    def find_sections(data):
        sections = []
        if isinstance(data, dict):
            # We found a section if it has a 'heading' and either 'blocks' (for places) or 'text' (for general notes)
            if 'heading' in data and ('blocks' in data or 'text' in data):
                sections.append(data)
            # Keep searching deeper
            for key, value in data.items():
                sections.extend(find_sections(value))
        elif isinstance(data, list):
            # If it's a list, search every item inside it
            for item in data:
                sections.extend(find_sections(item))
        return sections

    # 2. Extract all the sections using our helper function
    all_lists = find_sections(trip_json)
    
    for section in all_lists:
        # Get the heading (e.g., "Tokyo - Hotels" or "Notes")
        heading = section.get('heading', '').strip()
        if not heading:
            continue
            
        document_text += f"{heading.upper()}\n"
        document_text += "-" * len(heading) + "\n"
        
        # --- NEW: Extract section-level text (like your main "Notes" section) ---
        section_text_obj = section.get('text')
        if isinstance(section_text_obj, dict) and 'ops' in section_text_obj:
            # We don't strip() here so we keep Wanderlog's intended line breaks
            ops_text = "".join([op.get('insert', '') for op in section_text_obj['ops'] if isinstance(op.get('insert'), str)])
            if ops_text.strip():
                document_text += f"{ops_text}\n"
        
        # 3. Loop through the "blocks" array to find places
        blocks = section.get('blocks', [])
        place_index = 1
        
        for block in blocks:
            # If it's a Place
            if block.get('type') == 'place':
                place_info = block.get('place', {})
                place_name = place_info.get('name', 'Unknown Location')
                
                document_text += f"{place_index}. {place_name}\n"
                
                # Extract and indent the address
                address = place_info.get('formatted_address') or place_info.get('address')
                if address:
                    document_text += f"\tAddress: {address}\n"
                    
                # Extract and indent the notes attached to the place
                note = block.get('description') or block.get('note') or place_info.get('userNote')
                text_obj = block.get('text')
                
                if isinstance(text_obj, dict) and 'ops' in text_obj:
                    ops_text = "".join([op.get('insert', '') for op in text_obj['ops'] if isinstance(op.get('insert'), str)]).strip()
                    if ops_text:
                        document_text += f"\tNotes: {ops_text}\n"
                elif isinstance(note, str) and note.strip():
                    document_text += f"\tNotes: {note.strip()}\n"
                    
                place_index += 1
                
            # If there are standalone text blocks mixed into the places
            elif block.get('type') == 'text' or 'text' in block:
                text_obj = block.get('text')
                if isinstance(text_obj, dict) and 'ops' in text_obj:
                    ops_text = "".join([op.get('insert', '') for op in text_obj['ops'] if isinstance(op.get('insert'), str)])
                    if ops_text.strip():
                        document_text += f"{ops_text}\n"
                        
        # Add a blank line between sections
        document_text += "\n"
        
    # Sanitize the final string to prevent Google Docs from crashing
    safe_string = re.sub(r'[^\x20-\x7E\n\t]', '', document_text)
    
    return safe_string

def update_google_doc(text_content):
    """Clears the existing Google Doc and writes the new text content."""
    print("Authenticating with Google...")
    
    scopes = ['https://www.googleapis.com/auth/documents']
    creds = service_account.Credentials.from_service_account_file(
        GOOGLE_CREDENTIALS_FILE, scopes=scopes)
        
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
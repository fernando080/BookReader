import os
import re
from dotenv import load_dotenv

import xml.etree.ElementTree as ET
import pyodbc


# Load environment variables
load_dotenv()


def is_unwanted_section(text):
    # Search for words with at least 3 characters in the sections
    pattern = re.compile(r'\b[a-zA-Z]{3,}\b')
    return not bool(pattern.search(text))


def segment_text(xml_file_path, pdf_id, save_to_file=False, file_path=None):
    # Load the XML file
    tree = ET.parse(xml_file_path)
    root = tree.getroot()

    # Initialize variables for tracking
    temp_size = 0.000000001
    current_section = []
    sec_count = 0

    # We read all the text elements in the XML file
    for doc in root.findall('doc'):
        text_field = doc.find('field1').text
        font_field = doc.find('field2').text
        size_field = doc.find('field3').text

        # If the size varies positively with respect to the previous one, a new section will start.
        # 1. It is possible that we want to adjust the threshold of 0.9 in the if condition based on the results we observe.
        # If you find that you are getting too many sections, you could increase this threshold; if you find that you are
        # not getting enough, you could decrease it.
        # 2. This strategy assumes that the section titles will always be of a larger font size than the section text.
        # This may not be true in all documents, especially in documents with a more complex design. In these cases, other
        # text features, such as font style or bold, may need to be considered.
        # 3. We could also consider if there are other signs of a new section that can be used, in addition to the font size.
        # For example, sections may be separated by a blank line, or the section title may be on a line by itself.
        # the section title may be on a line by itself.

        if current_section and 0.0 < temp_size/float(size_field) < 0.85:
            current_section.append(text_field)
            # Process the current section
            section_text = process_section(current_section,
                                           pdf_id,
                                           save_to_file,
                                           file_path + '_' + str(sec_count) + '.txt')

            # Check if we got a section less than 100 characters
            if section_text:
                # If the section is less than 100 characters, start the new section with it
                current_section = [section_text]
            else:
                # If the section is greater than 100 characters, start a new section
                current_section = [text_field]

            temp_size = float(size_field)
            sec_count += 1
        else:
            # If the size has not varied much, continue with the current section
            current_section.append(text_field)
            temp_size = float(size_field)

    # Don't forget to process the last section
    if current_section:
        process_section(current_section,
                        pdf_id,
                        save_to_file,
                        file_path + '_' + str(sec_count) + '.txt',
                        is_last_section=True)
        sec_count += 1


def process_section(section, pdf_id, save_to_file=False, file_path=None, is_last_section=False):
    """
    Process the text of the section.
    If the section is too short and it's not the last one, it's returned.
    The text is saved to a file or printed.
    The section is then saved to the database.
    """
    # Extract the text from all the elements in the section
    section_text = ' '.join(elem for elem in section if len(elem) > 1)

    # If it's an unwanted section, end the function here
    if is_unwanted_section(section_text):
        return

    if not is_last_section and len(section_text) < 100:
        return section_text

    # Here you can do whatever you want with the section text
    if save_to_file:
        with open(file_path, 'w', encoding='utf-8') as fp:
            fp.write(section_text)
    else:
        print(section_text)

    subfile = file_path.split('\\')[-1]
    with pyodbc.connect(os.getenv('cnxn_str')) as cnxn:
        with cnxn.cursor() as cursor:
            # Save subfiles to the database
            cursor.execute("""
            INSERT INTO PDFSubFiles (PDF_ID, SUBFILE_NAME, IS_DELETED)
            VALUES (?, ?, 0)
            """, pdf_id, subfile)
            cnxn.commit()

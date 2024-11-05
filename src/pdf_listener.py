import os
import re
import json
import queue
import xml.etree.ElementTree as ET
from threading import Thread, Event
from typing import List
from dotenv import load_dotenv

import tiktoken
from pdfminer.converter import PDFPageAggregator
from pdfminer.layout import LAParams, LTTextBox, LTTextLine
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
from pdfminer.pdfpage import PDFPage

# from chatgpt_responses import chatgpt_response
from chatgpt_responses import create_conversation_chain, compose_input_with_relevant_info
from env import num_msgs_to_include_in_buffer, encabezado, MAX_TOKENS, MODEL, prefix_info_phrase
from infomation_retrival_for_questions import read_files, get_most_relevant_docs
from typograph_text_spliter import segment_text

# Load environment variables
load_dotenv()


def remove_illegal_chars(input_string):
    # Esta expresión regular seleccionará todos los caracteres ilegales en XML
    illegal_xml_chars_re = re.compile(u'[\x00-\x08\x0b-\x1f\x7f-\x84\x86-\x9f\ud800-\udfff\ufdd0-\ufddf\ufffe-\uffff]')

    return illegal_xml_chars_re.sub('', input_string)


def extract_text_with_font_info(pdf_path):
    resource_manager = PDFResourceManager()
    device = PDFPageAggregator(resource_manager, laparams=LAParams())
    interpreter = PDFPageInterpreter(resource_manager, device)

    extracted_text = []

    with open(pdf_path, 'rb') as fp:
        for page in PDFPage.get_pages(fp):
            interpreter.process_page(page)
            layout = device.get_result()

            for element in layout:
                if not isinstance(element, LTTextBox):
                    continue

                for text_line in element:
                    if not isinstance(text_line, LTTextLine):
                        continue

                    text = text_line.get_text()
                    font, size = None, None

                    for character in text_line:
                        if hasattr(character, 'fontname'):
                            font = character.fontname
                            size = character.size
                            break

                    if font is not None and size is not None:
                        extracted_text.append({'text': text, 'font': font, 'size': size})

    return extracted_text


def extract_paragraphs_with_font_info(pdf_path):
    resource_manager = PDFResourceManager()
    device = PDFPageAggregator(resource_manager, laparams=LAParams())
    interpreter = PDFPageInterpreter(resource_manager, device)

    extracted_paragraphs = []

    with open(pdf_path, 'rb') as fp:
        for page in PDFPage.get_pages(fp):
            interpreter.process_page(page)
            layout = device.get_result()

            for element in layout:
                if not isinstance(element, LTTextBox):
                    continue

                paragraph_text = element.get_text()
                font, size = None, None

                for text_line in element:
                    for character in text_line:
                        if hasattr(character, 'fontname'):
                            font = character.fontname
                            size = character.size
                            break
                    if font is not None and size is not None:
                        break

                if font is not None and size is not None:
                    extracted_paragraphs.append({'text': paragraph_text})

    return extracted_paragraphs


def extract_and_convert_to_xml(cnxn, cursor, file_path, pdf_id):
    print(f'Extracting text from - {file_path}')
    extracted_text_with_font_info = extract_text_with_font_info(file_path)

    root = ET.Element("root")
    for text_info in extracted_text_with_font_info:
        doc = ET.SubElement(root, "doc")
        ET.SubElement(doc, "field1", name="text").text = text_info['text']
        ET.SubElement(doc, "field2", name="font").text = text_info['font']
        ET.SubElement(doc, "field3", name="size").text = str(text_info['size'])

    # Replace special characters in filename
    filename_dir = re.sub(r'\W+', '_', os.path.split(os.path.splitext(file_path)[0])[1])
    parent_dir = os.path.dirname(file_path)
    complete_dir = os.path.join(parent_dir, filename_dir)

    xml_file_path = os.path.join(complete_dir, filename_dir + '.xml')
    text_files_dir = os.path.join(complete_dir, filename_dir)

    # Ensure the directory exists
    os.makedirs(os.path.dirname(xml_file_path), exist_ok=True)

    # Clean XML content
    xml_content = ET.tostring(root, encoding='utf-8').decode('utf-8')
    clean_xml_content = remove_illegal_chars(xml_content)

    # Write cleaned content to the file
    with open(xml_file_path, 'w', encoding='utf-8') as file:
        file.write(clean_xml_content)

    print(f'xml extracted and saved in - {xml_file_path} \n')

    # Save XML to the database
    cursor.execute("""
    INSERT INTO PDFSubFiles (PDF_ID, SUBFILE_NAME, IS_DELETED)
    VALUES (?, ?, 0)
    """, pdf_id, xml_file_path)
    cnxn.commit()

    # Split text in segments
    print(f'Splitting text in segments')
    segment_text(xml_file_path, pdf_id, save_to_file=True, file_path=text_files_dir)

    print(
        f'Text splitted and saved in - {os.path.join(os.path.splitext(file_path)[0], os.path.split(os.path.splitext(file_path)[0])[1])} \n')


class UserInputHandler(Thread):
    def __init__(self,
                 cnxn,
                 cursor,
                 path,
                 chat_id: int,
                 user_id: int = None,
                 inputs: List = None,
                 ):
        Thread.__init__(self)
        self.cnxn = cnxn
        self.cursor = cursor
        self.chat_id = chat_id
        self.main_path = os.path.join(path, str(user_id))
        self.selected_pdf_id = None
        self.finished = Event()
        self.input_question = None
        self.pdf_slides = None
        self.user_id = user_id

        # Define the queues
        self.questions = ''
        self.question_tokens = None
        self.answers = ''
        self.answers_tokens = None

        # Define the tokenizer
        self.tokenizer = tiktoken.encoding_for_model(MODEL)

        # Create the conversation
        print("Creating conversation...")
        self.input_msgs_entries = inputs if inputs else []
        self.conversation = create_conversation_chain(inputs=self.input_msgs_entries,
                                                      num_msgs=num_msgs_to_include_in_buffer)

    def __getstate__(self):
        # Define which attributes to serialize
        state = self.__dict__.copy()
        # Removes the attributes that are not serializable
        del state['tokenizer']  # Asume que 'tokenizer' no es serializable
        return state

    def __setstate__(self, state):
        # Restores the attributes from the serialized state
        self.__dict__.update(state)
        # Restores the attributes that are not serializable
        self.tokenizer = tiktoken.encoding_for_model(MODEL)

    def add_question(self, question: str):
        self.questions = question

        if self.selected_pdf_id is not None:
            self.llm_conversation_with_memory()
        else:
            raise (ValueError("Question added, but no document selected"))

    def get_next_question(self):
        return self.questions

    def add_answer(self, answer: str):
        self.answers = answer

    def get_next_answer(self):
        return self.answers

    def add_pdf(self, pdf_id, pdfs_path):
        self.pdf_slides = pdfs_path

        if str(pdf_id) in self.pdf_slides:
            self.selected_pdf_id = pdf_id
        else:
            raise ValueError(f"Invalid document ID: {pdf_id}")

    def add_chat_id(self, chat_id):
        self.chat_id = chat_id

    def llm_conversation_with_memory(self):
        """
        Function to create a conversation with the OpenAI llm model defined in env.py using the langchain API
        """
        self.input_question = self.get_next_question()
        if self.input_question is None:
            return
        # Read the files from the directory
        corpus, corpus_tokenized, filenames = read_files(self.pdf_slides[str(self.selected_pdf_id)],
                                                         self.user_id)

        # Check if the total length of the documents is less than the maximum number of tokens
        total_length = 0
        for tokens_in_slice in corpus_tokenized:
            total_length += len(tokens_in_slice)

        if total_length < MAX_TOKENS:
            # If it is less, it is not necessary to filter the documents, we use float('Inf') to indicate that all are
            # relevant and will be added to the prompt, skipping the treshold defined in env.py (BM25_threshold)
            relevant_info = [(filename, float('Inf')) for filename in filenames]
        else:
            # If it is greater, we filter the documents using BM25
            relevant_info = get_most_relevant_docs(self.input_question, corpus_tokenized, filenames)

        # Add the relevant information to the prompt
        print(f"Relevant info for question: {self.input_question}")
        msgs, not_found_info, total_tokens = compose_input_with_relevant_info(self.main_path,
                                                                              relevant_info,
                                                                              prefix_info_phrase)

        # if we have relevant information, we add it to the prompt
        summary = ''
        if not not_found_info:
            print(f"Found relevant info for question: {self.input_question}")
            try:
                # We can use the variable total tokens to iterate over the total of the messages if they do not
                # exceed the token limit defined in env.py (MAX_TOKENS).
                # For now we only take the last message
                acc_tokens_in_msgs = 0
                if total_tokens < MAX_TOKENS:
                    for msg in msgs:
                        summary = self.conversation.predict(input=msg)
                        in_out_json = json.dumps({"input": msg, "output": summary})
                        # We save the intermediate prompt with the content related to the question
                        self.cursor.execute("""
                        INSERT INTO MESSAGES (USER_ID, DATE, TYPE_OF_MESSAGE, MESSAGE, PDF_ID, NUMBER_OF_TOKENS, CHAT_ID)
                        VALUES (?, GETDATE(), 'F', ?, ?, ?, ?)
                        """, self.user_id,
                                       in_out_json,
                                       self.selected_pdf_id,
                                       len(self.tokenizer.encode(msg)),
                                       self.chat_id)
                        self.cnxn.commit()

                else:
                    for msg in msgs:
                        if acc_tokens_in_msgs + len(self.tokenizer.encode(msg)) < MAX_TOKENS:
                            summary = self.conversation.predict(input=msg) + '. '
                            acc_tokens_in_msgs += len(self.tokenizer.encode(msg))
                            # We save the intermediate prompt with the content related to the question
                            self.cursor.execute("""
                            INSERT INTO MESSAGES (USER_ID, DATE, TYPE_OF_MESSAGE, MESSAGE, PDF_ID, NUMBER_OF_TOKENS, CHAT_ID)
                            VALUES (?, GETDATE(), 'F', ?, ?, ?, ?)
                            """, self.user_id,
                                           msgs[-1],
                                           self.selected_pdf_id,
                                           len(self.tokenizer.encode(msg)),
                                           self.chat_id)
                            self.cnxn.commit()

                print("\nAdded message to the conversation. Tokens: ", len(self.tokenizer.encode(msgs[-1])))
            except Exception as e:
                print(f"\nError processing message.\n Tokens: {len(self.tokenizer.encode(msgs[-1]))}.")
                print(e)

            # Add the user question to the conversation
            prompt = encabezado + self.input_question
            response = self.conversation.predict(input=prompt)
        else:
            prompt = self.input_question
            response = self.conversation.predict(input=prompt)

        self.question_tokens = self.tokenizer.encode(prompt)
        self.answers_tokens = self.tokenizer.encode(response)

        self.add_answer(response)

        self.cursor.execute("""
        INSERT INTO MESSAGES (USER_ID, DATE, TYPE_OF_MESSAGE, MESSAGE, PDF_ID, NUMBER_OF_TOKENS, CHAT_ID)
        VALUES (?, GETDATE(), 'P', ?, ?, ?, ?)
        """, self.user_id,
                       prompt,
                       self.selected_pdf_id,
                       len(self.tokenizer.encode(prompt)),
                       self.chat_id)
        self.cnxn.commit()

        self.cursor.execute("""
        INSERT INTO MESSAGES (USER_ID, DATE, TYPE_OF_MESSAGE, MESSAGE, PDF_ID, NUMBER_OF_TOKENS, CHAT_ID)
        VALUES (?, GETDATE(), 'L', ?, ?, ?, ?)
        """, self.user_id,
                       response,
                       self.selected_pdf_id,
                       len(self.tokenizer.encode(response)),
                       self.chat_id)
        self.cnxn.commit()

        print(self.conversation.memory.buffer)

        return

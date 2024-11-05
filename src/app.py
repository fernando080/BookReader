import os
import re
import time
import json
import shutil
import pyodbc

from dotenv import load_dotenv
from env import path_to_listen as path
from env import num_msgs_to_include_in_buffer as msgs_limit
from env import MAX_RETRIES, SLEEP_TIME
from flask import Flask, request, abort, jsonify
from pdf_listener import UserInputHandler, extract_and_convert_to_xml
from werkzeug.utils import secure_filename
from multiprocessing import Process

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('BOOK_READER_API_SECRET_KEY')
app.config['UPLOAD_FOLDER'] = path


def get_database_connection():
    # Establishes the connection with the database
    for _ in range(MAX_RETRIES):
        try:
            cnxn = pyodbc.connect(os.getenv('cnxn_str'))
            return cnxn, cnxn.cursor()
        except pyodbc.OperationalError:
            print(f"Connection error. Attempt {_ + 1} of {MAX_RETRIES}")
            time.sleep(SLEEP_TIME)
    return None, None  # If we reach here, all connection attempts have failed


def check_api_key(api_key):
    # Check if the client sent an API key in the request
    return api_key == os.getenv('BOOK_READER_API_SECRET_KEY')


def check_and_create_user(cnxn, cursor, user_id):
    # Check if user_id exists in the database
    cursor.execute("""
    SELECT USER_ID_FROM_UI
    FROM USERS
    WHERE USER_ID_FROM_UI = ?
    """, user_id)
    row = cursor.fetchone()

    # If user_id does not exist, insert it
    if row is None:
        try:
            # Tries to insert the user, if it already exists the unique key violation exception is caught
            cursor.execute("""
                INSERT INTO USERS (DATE, USER_ID_FROM_UI)
                VALUES (GETDATE(), ?)
                """, user_id)
            cnxn.commit()
        except Exception as e:
            # Here you can capture and handle specifically the "unique key violation" exception
            # if your database system allows it. Otherwise, we handle all errors generally.
            print(f"Error inserting user {user_id}: {e}")
            # Revert the transaction
            cnxn.rollback()
            cursor.close()
            cnxn.close()


def get_pdf_id(cursor, user_id):
    cursor.execute("""
    SELECT TOP 1 PDF_ID
    FROM PDFFiles
    WHERE USER_ID = ? 
    AND IS_DELETED = 0
    ORDER BY PDF_ID DESC
    """, user_id)
    row = cursor.fetchone()

    return str(row.PDF_ID) if row else None


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() == 'pdf'


def handle_new_pdf(cnxn, cursor, pdf_id, pdf_path, user_id):
    print(f'New file - {pdf_path}')

    # Replace special characters in filename
    pdf_foldername = re.sub(r'\W+', '_', os.path.split(os.path.splitext(pdf_path)[0])[1]) + '.pdf'
    # os.path.split(os.path.splitext(file_path)[0])[1] Accesses the file name without extension
    # parent_dir = os.path.dirname(pdf_path)

    # Check if file exists in database
    cursor.execute("""
    SELECT PDF_ID
    FROM PDFFiles
    WHERE FILE_NAME = ? AND USER_ID = ? AND IS_DELETED = 0
    """, pdf_foldername, user_id)
    row = cursor.fetchone()

    if row is None:
        try:
            # Insert new record in PDFFiles and get the inserted PDF_ID
            cursor.execute("""
                    INSERT INTO PDFFiles (FILE_NAME, UPLOAD_DATE, USER_ID, IS_DELETED, IS_PROCESSED, PDF_ID)
                    OUTPUT INSERTED.PDF_ID
                    VALUES (?, GETDATE(), ?, 0, 0, ?)
                    """, (pdf_foldername, user_id, pdf_id))
            pdf_id = cursor.fetchone()[0]
            cnxn.commit()

            # Process the uploaded file
            extract_and_convert_to_xml(cnxn, cursor, pdf_path, pdf_id)

            # Mark the file as processed in the database
            cursor.execute("""
            UPDATE PDFFiles
            SET IS_PROCESSED = 1
            WHERE PDF_ID = ?
            """, pdf_id)
            cnxn.commit()

            return 'File uploaded and processed', 200

        except Exception as e:
            # Handle any database error
            cnxn.rollback()
            cursor.close()
            cnxn.close()

            # We remove the pdf file (<user_is>/file_name.pdf) if it exists
            if pdf_foldername and os.path.exists(pdf_foldername):
                os.remove(pdf_foldername)

            # We remove the directory (<user_is>/file_name) of the pdf if it exists using the file path without the extension
            if pdf_foldername:
                dirpath = os.path.splitext(pdf_foldername)[0]
                if os.path.exists(dirpath):
                    shutil.rmtree(dirpath)

            abort(500, description=f"Error processing the PDF file: {e}")
    else:
        try:
            cursor.close()
        except pyodbc.ProgrammingError:
            pass
        try:
            cnxn.close()
        except pyodbc.ProgrammingError:
            pass

        abort(400, description="File already exists")


def get_last_n_messages(cursor, user_id, pdf_id, chat_id, n):
    # Get the last n messages from the database
    cursor.execute("""
    SELECT TOP (?) DATE, TYPE_OF_MESSAGE, MESSAGE, PDF_ID
    FROM MESSAGES
    WHERE CHAT_ID = ? 
    AND USER_ID = ? 
    AND PDF_ID = ? 
    ORDER BY DATE DESC
    """, n, chat_id, user_id, pdf_id)
    rows = cursor.fetchall()

    if not rows:
        return None

    inputs = []
    answers = []
    questions = []
    for row in rows:
        if row.TYPE_OF_MESSAGE == 'F':  # Message from user
            message = json.loads(row.MESSAGE)
            inputs.append(({"input": message["input"]}, {"output": message["output"]}))
        elif row.TYPE_OF_MESSAGE == 'L' or row.TYPE_OF_MESSAGE == 'P':  # Reply from system
            if row.TYPE_OF_MESSAGE == 'L':
                answers.append(row.MESSAGE)
            else:
                questions.append(row.MESSAGE)

        # Check if the length of the lists of questions and answers is the same
        if len(answers) == len(questions):
            inputs.append(({"input": questions[-1]}, {"output": answers[-1]}))

    return inputs


@app.route('/users/<user_id>/documents/<pdf_id>', methods=['POST'])
def upload_file(user_id, pdf_id):
    file = request.files['file']
    api_key = request.headers.get('X-Api-Key')

    if not api_key:
        abort(401, description="Missing API key")

    # Check that the API key is valid
    if not check_api_key(api_key):
        abort(401, description="Invalid API key")

    if not user_id or 'file' not in request.files:
        abort(404, description="Missing required data")

    # Check if the post request has the file part
    if file in ['', None]:
        abort(400, description="Missing file")

    # Get database connection
    cnxn, cursor = get_database_connection()

    # Declare variables
    filepath = None

    try:
        # Check if user exists and create if not
        check_and_create_user(cnxn, cursor, user_id)

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            user_folder = os.path.join(app.config['UPLOAD_FOLDER'], user_id)

            # If the user folder does not exist, we create it
            if not os.path.exists(user_folder):
                os.makedirs(user_folder)

            # Save the file in the user folder by standardizing its name and replacing all special characters
            # with underscores and constructing the complete path
            filename = re.sub(r'\W+', '_', os.path.splitext(filename)[0]) + '.pdf'
            filepath = os.path.join(user_folder, filename)

            # Save the file on the server using the main process
            file.save(filepath)

            # Use multiprocessing to process the file in the background
            process = Process(target=process_file, args=(pdf_id, filepath, user_id,))
            process.start()

            cursor.close()
            cnxn.close()

            return jsonify({
                'status': 200,
                'user_id': user_id,
                'pdf_id': pdf_id,
                'filename': filename,
                'message': 'File uploaded and will be processed in the background'
            })
        else:
            # Close the database connection
            cursor.close()
            cnxn.close()
            abort(400, description="File extension not allowed")

    except Exception as e:
        # Handle any database error
        try:
            cursor.close()
        except pyodbc.ProgrammingError:
            pass
        try:
            cnxn.close()
        except pyodbc.ProgrammingError:
            pass

        # Remove the pdf file (<user_is>/file_name.pdf) if it exists
        if filepath and os.path.exists(filepath):
            os.remove(filepath)

        # Remove the directory (<user_is>/file_name) of the pdf if it exists using filepath without the extension
        if filepath:
            dirpath = os.path.splitext(filepath)[0]
            if os.path.exists(dirpath):
                shutil.rmtree(dirpath)

        abort(500, description=f"Error uploading file: {e}")


def process_file(pdf_id, filepath, user_id):
    cnxn, cursor = get_database_connection()
    try:
        api_mess, ret_code = handle_new_pdf(cnxn, cursor, pdf_id, filepath, user_id)
        if ret_code != 200:
            abort(ret_code, description=api_mess)
    finally:
        try:
            cursor.close()
        except pyodbc.ProgrammingError:
            pass
        try:
            cnxn.close()
        except pyodbc.ProgrammingError:
            pass


@app.route('/users/<user_id>/documents/<pdf_id>/chats/<chat_id>/question', methods=['POST'])
def get_document_and_question(user_id, pdf_id, chat_id):
    print('New request for document and question')
    data = request.get_json()

    if not data:
        abort(400, description="Missing required data")

    # Retrieve the question from the request
    question = data.get('question')

    # Retrieve the API key from the request
    api_key = request.headers.get('X-Api-Key')

    print(f'question - {question}')
    print(f'document - {pdf_id}')
    print(f'user - {user_id}')
    print(f'chat - {chat_id}')
    print(f'API key - {api_key}')

    if not api_key:
        abort(401, description="Missing API key")

    # Check that the API key is valid
    if not check_api_key(api_key):
        abort(401, description="Invalid API key")

    # Validate the user
    if user_id not in os.listdir(path):
        abort(400, description="Invalid the user_id does not exist on the server")

    # Validate pdf_id
    if not pdf_id:
        abort(400, description="Invalid pdf_id")

    # Validate chat_id
    if not chat_id:
        abort(400, description="Invalid chat_id")

    # Get the database connection
    cnxn, cursor = get_database_connection()

    try:
        # Check if the document exists in the database and has been processed correctly
        cursor.execute("""
        SELECT PDF_ID
        FROM PDFFiles
        WHERE PDF_ID = ? 
        AND IS_DELETED = 0
        AND IS_PROCESSED = 1
        """, pdf_id)
        row = cursor.fetchone()

        # Return a 202 if the document has not been processed yet
        if row is None:
            return jsonify({
                'status': 202,
                'user_id': user_id,
                'pdf_id': pdf_id,
                'chat_id': chat_id,
                'message': 'Document is not processed yet'
            })

        # Retrieve the filename of the document from the database using pdf_id
        cursor.execute("""
        SELECT FILE_NAME
        FROM PDFFiles
        WHERE PDF_ID = ? AND IS_DELETED = 0
        """, pdf_id)
        row = cursor.fetchone()

        if row is None:
            cursor.close()
            cnxn.close()
            abort(400, description="Invalid pdf_id")

        # Retrieve the dictionary of documents belonging to the user from the database
        cursor.execute("""
        SELECT PDF_ID, FILE_NAME
        FROM PDFFiles
        WHERE IS_DELETED = 0 AND USER_ID = ?
        """, user_id)

        rows = cursor.fetchall()

        pdf_slides = {}
        for row in rows:
            pdf_slides[str(row.PDF_ID)] = os.path.splitext(row.FILE_NAME)[0]

        # Validate the document and the question
        if not pdf_id or not question:
            cursor.close()
            cnxn.close()
            abort(400, description="Missing pdf_id or question")

        # Validate that the document is in the dictionary
        if pdf_id not in pdf_slides:
            cursor.close()
            cnxn.close()
            abort(400, description="Invalid pdf_id")

        inputs = get_last_n_messages(cursor, user_id, pdf_id, chat_id, msgs_limit*2)
        if inputs:
            user_input_handler = UserInputHandler(cnxn,
                                                  cursor,
                                                  app.config['UPLOAD_FOLDER'],
                                                  chat_id,
                                                  user_id,
                                                  inputs,)
        else:
            user_input_handler = UserInputHandler(cnxn,
                                                  cursor,
                                                  app.config['UPLOAD_FOLDER'],
                                                  chat_id,
                                                  user_id)

        # Add the chat_id and the state of the chat
        user_input_handler.add_chat_id(chat_id=chat_id)

        # Add the document and question
        user_input_handler.add_pdf(pdf_id=pdf_id, pdfs_path=pdf_slides)

        # Add the question to the queue
        user_input_handler.add_question(question=question)

        # Wait for the answer (this line will block until there is an available answer)
        answer = user_input_handler.get_next_answer()

        # Close the database connection
        cursor.close()
        cnxn.close()

        return jsonify({
                'status': 200,
                'user_id': user_id,
                'pdf_id': pdf_id,
                'chat_id': chat_id,
                'Question': question,
                'Answer': answer
            })

    except Exception as e:
        try:
            cursor.close()
        except pyodbc.ProgrammingError:
            pass
        try:
            cnxn.close()
        except pyodbc.ProgrammingError:
            pass
        abort(500, description=f"Internal server error - {e}")


@app.route('/users/<user_id>/documents', methods=['GET'])
def get_user_documents(user_id):
    # Obtain the API key from the query parameters
    api_key = request.headers.get('X-Api-Key')

    # Check if the API key is valid
    if not check_api_key(api_key):
        abort(401, description="Invalid API key")  # Unauthorized

    # Get the database connection
    cnxn, cursor = get_database_connection()

    try:
        # Verify if the user exists in the database
        cursor.execute("""
        SELECT USER_ID_FROM_UI
        FROM USERS
        WHERE USER_ID_FROM_UI = ?
        """, (user_id,))
        row = cursor.fetchone()

        # If the user does not exist, return an error message
        if row is None:
            cursor.close()
            cnxn.close()
            abort(404, description="User not found")

        # Retrieve the list of documents for the user
        cursor.execute("""
        SELECT PDF_ID, FILE_NAME, IS_PROCESSED
        FROM PDFFiles
        WHERE USER_ID = ? AND IS_DELETED = 0
        """, user_id)
        rows = cursor.fetchall()

        # If there are no documents, return an error message
        if not rows:
            cursor.close()
            cnxn.close()
            documents = []
        else:
            # Create a list of dictionaries with the data of the documents
            documents = [{"id": str(row.PDF_ID), "filename": row.FILE_NAME, "isReady": True if int(row.IS_PROCESSED) == 1 else False} for row in rows]

            # Close the database connection
            cursor.close()
            cnxn.close()

        # Return the list of documents in JSON format
        return jsonify(documents)

    except Exception as e:
        try:
            cursor.close()
        except pyodbc.ProgrammingError:
            pass
        try:
            cnxn.close()
        except pyodbc.ProgrammingError:
            pass
        abort(500, description=f"Internal server error - {e}")


@app.route('/users/<user_id>/documents/<pdf_id>', methods=['DELETE'])
def delete_file(user_id, pdf_id):

    # Delete a file from the database
    api_key = request.headers.get('X-Api-Key')

    if not api_key:
        abort(401, description="Missing API key")

    # Check that the API key is valid
    if not check_api_key(api_key):
        abort(401, description="Invalid API key")

    # Check that the necessary user_id data was received
    if not user_id:
        abort(400, description="Missing user_id in request")

    # Check that the necessary data was received
    if not pdf_id:
        abort(400, description="Missing pdf_id in request")

    # Get the database connection
    cnxn, cursor = get_database_connection()

    try:
        # Obtain the filename from the database using the file_id in a select
        cursor.execute("""
        SELECT FILE_NAME, IS_PROCESSED
        FROM PDFFiles
        WHERE PDF_ID = ?
        """, pdf_id)
        row = cursor.fetchone()
        filename = None

        # Control that the file has finished processing
        if row.IS_PROCESSED == 0:
            # If the file has not yet been processed, we send a 202 so that the client can retry later
            cursor.close()
            cnxn.close()
            return jsonify({
                'status': 202,
                'user_id': user_id,
                'pdf_id': pdf_id,
                'message': 'Document is not processed yet'
            })

        if row:
            filename = row.FILE_NAME
        else:
            cursor.close()
            cnxn.close()
            abort(404, description="File ID not found in database")

        if not filename:
            cursor.close()
            cnxn.close()
            abort(404, description="Missing filename in database with this PDF_ID")

        user_folder = os.path.join(app.config['UPLOAD_FOLDER'], str(user_id))
        filepath = os.path.join(user_folder, filename)

        # Proceed to delete the file, its folder, and its record in the database
        folder_path = os.path.join(user_folder, re.sub(r'\W+', '_', os.path.splitext(filename)[0]))

        if os.path.exists(folder_path):
            try:
                # Check that the file exists before attempting to delete it
                if os.path.exists(filepath):
                    # Delete the file
                    os.remove(filepath)
                else:
                    cnxn.rollback()
                    cursor.close()
                    cnxn.close()
                    abort(404, description="File pdf not found in Server")

                # Only delete the folder if the database updates were successful
                shutil.rmtree(folder_path)

                # Check if the user folder is empty
                if not os.listdir(user_folder):
                    # Execute a query to mark the file as deleted
                    cursor.execute("""
                    UPDATE PDFFiles
                    SET IS_DELETED = 1, DELETED_DATE = GETDATE()
                    WHERE PDF_ID = ?;
                    """, pdf_id)
                    cnxn.commit()

                    # Execute a query to mark the subfiles as deleted
                    cursor.execute("""
                    UPDATE PDFSubFiles
                    SET IS_DELETED = 1, DELETED_DATE = GETDATE()
                    WHERE PDF_ID = ?;
                    """, pdf_id)
                    cnxn.commit()
                else:
                    cursor.close()
                    cnxn.close()
                    abort(500, description="User folder isnot empty, something is wrong deleting the pdf's file and folder")

            except Exception as e:
                # If something goes wrong, revert the database operations
                cnxn.rollback()

                # Close the database connection
                cursor.close()
                cnxn.close()

                abort(500, description=f"Error deleting file and subfiles in database or server: {e}")
        else:
            # Close the database connection
            cursor.close()
            cnxn.close()

            abort(404, description="File folder not found in Server")

        # Close the database connection
        cursor.close()
        cnxn.close()

        return jsonify({
            'status': 200,
            'user_id': user_id,
            'pdf_id': pdf_id,
            'filename': filename,
            'message': 'File and folder deleted'
        })

    except Exception as e:
        # Close the database connection
        try:
            cursor.close()
        except pyodbc.ProgrammingError:
            pass
        try:
            cnxn.close()
        except pyodbc.ProgrammingError:
            pass
        abort(500, description=f"Internal server error - {e}")


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)  # Start running your server on port 5000

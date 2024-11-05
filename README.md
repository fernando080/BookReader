# BookReader
BookReader is a docker api application designed to manage, process and answer questions about PDF documents. The application allows users to upload PDF files, which are then processed to extract information, process the text blocks, include a system to relate user question and related them with the text blocks, and give accurate answers using the pdf information as context.

THE TARGET LANGUAGE OF THIS PROJECT IS SPANISH.

**IMPORTANT**: This project is probably outdated, and it is not recommended to use it in a production environment. It is recommended to use it as a reference for building a similar application.

## Key Features:
1. PDF Upload and Management:
    * Users can upload PDF documents to their personalized folders on the server.
    * The system supports background processing of PDF files to extract content and convert it into XML format.
2. API Integration:
    * The application is equipped with an API that validates user requests using API keys, ensuring secure interactions.
    * Provides endpoints for uploading, deleting, and querying documents, enhancing user interaction with their stored data.
3. Database Interaction:
    * Utilizes a database to store user information and document metadata, including processing status and user-specific data.
    * Features robust error handling and transaction management to ensure data integrity and consistent performance.
4. Basic User Interaction Query Handling:
    * Basic user interection via terminal and  processing of user queries related to documents, capable of fetching, and displaying messages and document-related information.
    * Implements a chat-like interface using the terminal where users can pose questions regarding their documents and receive immediate responses.
5. Security and File Management:
    * Employs secure file handling techniques to prevent unauthorized access and ensure safe storage of sensitive information.
    * Uses environment variables and secure configurations to manage database connections and other critical settings.

## Technology Stack:

- Backend: Python, Flask (for API creation and server-side logic)
- Database: Utilizes SQL Server with ODBC connections for robust data management.
- File Handling: Implements multiprocessing for efficient file processing and Python libraries for file manipulation.
- Security: API key authentication, secure file upload handling using Werkzeug's secure_filename function.

This project is particularly useful for organizations that require an efficient way to store, process, and retrieve information from PDF documents, providing a secure and user-friendly environment for document management.

## Usage
1. First is create docker image:
> docker build -t app .
2. Then run the image, use the volume and expose the port:
> docker run -p 5000:5000 -v /home/user/pdfs_storages:/pdfs_storages --env-file .env app
3. Save the log from the container
> docker logs [CONTAINER_ID] > output.log
4. If you want to add the logs you can user '>>'
> docker logs [CONTAINER_ID] >> output.log
5. If you want to see the logs on stram you can use '-f'
> docker logs -f [CONTAINER_ID] > output.log
6. To execute te container when the vm restarts or is stopped you can use:
> docker update --restart=always <nombre_del_contenedor>

or

> docker update --restart=unless-stopped <nombre_del_contenedor> app
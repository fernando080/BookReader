# Use an official Python base image
FROM python:3.10

# Install necessary dependencies for pyodbc
RUN apt-get update && apt-get install -y unixodbc-dev gpg curl apt-transport-https debconf-utils

# Add keys and repositories for Microsoft ODBC Driver 18 for SQL Server
RUN curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add -
RUN curl https://packages.microsoft.com/config/debian/10/prod.list > /etc/apt/sources.list.d/mssql-release.list

# Install Microsoft ODBC Driver 18 for SQL Server
RUN apt-get update && ACCEPT_EULA=Y apt-get install -y msodbcsql18

# Create a volume for document persistence
RUN mkdir /pdf_storage

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install dependencies
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Install Gunicorn
RUN pip install gunicorn gevent

# Copy the application code into the container
# Assuming all relevant source code is in the src directory
COPY src/ .

# Expose port
EXPOSE 5000

# Run the application with Gunicorn
CMD ["gunicorn", "--worker-class=gevent", "--workers=12", "--timeout=300", "--bind", "0.0.0.0:5000", "main.app:app"]
# Dockerfile for main tracking service
FROM python:3.11.9

# Set the working directory in the container
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . /app/

# Install any needed packages specified in requirements.txt
RUN apt-get update && apt-get install -y libgl1-mesa-glx
RUN pip install -r requirements.txt


# Run the service
CMD ["python", "index.py"]
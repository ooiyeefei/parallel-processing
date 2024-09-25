# Dockerfile for yolov8 model
FROM python:3.11.9

# Set the working directory in the container
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . /app/

# Install any needed packages specified in requirements.txt
RUN apt-get update && apt-get install -y libgl1-mesa-glx
RUN pip install -r requirements.txt

# Expose port
EXPOSE 5000

# Run the service
CMD ["python", "yolov8_service.py"]
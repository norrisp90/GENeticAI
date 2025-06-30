#!/bin/bash

# Exit on any error
set -e

# Print debug information
echo "Starting Chainlit application..."
echo "Python version: $(python --version)"
echo "Working directory: $(pwd)"
echo "Contents of current directory:"
ls -la

# Install dependencies
echo "Installing dependencies..."
pip install --no-cache-dir -r requirements.txt

# Set environment variables for production
export CHAINLIT_HOST=0.0.0.0
export CHAINLIT_PORT=${PORT:-8000}

# Print environment info (without sensitive values)
echo "Environment setup:"
echo "HOST: $CHAINLIT_HOST"
echo "PORT: $CHAINLIT_PORT"

# Start the Chainlit application
echo "Starting Chainlit app..."
exec chainlit run app.py --host $CHAINLIT_HOST --port $CHAINLIT_PORT --headless
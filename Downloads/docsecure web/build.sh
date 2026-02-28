#!/bin/bash
# Build script for Render deployment
# Installs system dependencies required by the application

echo "Installing system dependencies..."
apt-get update
apt-get install -y libzbar0

echo "System dependencies installed successfully!"

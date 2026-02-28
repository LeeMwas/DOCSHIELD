#!/bin/bash
# Build script for Render deployment
# Installs system dependencies required by the application
# Note: On Render, use nativePackages in render.yaml instead
# This script is a fallback for local testing or other environments

echo "Installing system dependencies..."

# Check if running with sudo available
if command -v sudo &> /dev/null; then
    sudo apt-get update
    sudo apt-get install -y libzbar0
elif [ "$EUID" -eq 0 ]; then
    # Running as root
    apt-get update
    apt-get install -y libzbar0
else
    echo "Warning: Cannot install system packages without sudo or root access"
    echo "On Render, ensure nativePackages is configured in render.yaml"
    exit 1
fi

echo "System dependencies installed successfully!"

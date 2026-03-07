"""
Run this script with Fooocus running at localhost:7865 to discover
the available Gradio API endpoints, parameter names, and types.

Usage: python scripts/discover_api.py

Look for the endpoint that handles the "Input Image" / "Upscale or Vary" tab.
The api_name and parameter list you find here go into fooocus_client.py.
"""
from gradio_client import Client

client = Client("http://localhost:7865/")
client.view_api()

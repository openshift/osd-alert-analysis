"""
Import this to load alert-analysis's configuration

Create .env file to override actual environmental variables
"""
from os import getenv
from dotenv import load_dotenv

load_dotenv()

PD_API_TOKEN = getenv("AA_PD_API_TOKEN")
PD_TEAMS = getenv("AA_PD_TEAMS").split(":")
RO_DB_STRING = getenv("AA_RO_DB_STRING")
RW_DB_STRING = getenv("AA_RW_DB_STRING")
QUESTION_CLASSES = getenv("AA_QUESTION_CLASSES").split(":")


import logging
from app.services.troubleshooter import Troubleshooter

logging.basicConfig(level=logging.INFO)

troubleshooter = Troubleshooter()

issue = (
    "My Spring Boot application cannot connect to PostgreSQL. I am getting a connection refused error."
)

response = troubleshooter.troubleshoot(issue)

print("\n--- Troubleshooting Response ---\n")
print(response)

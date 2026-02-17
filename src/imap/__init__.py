"""IMAP package"""
from src.imap.imap_client import GmailIMAPClient, EmailMessage, create_imap_client_from_config
from src.imap.outlook_imap_client import OutlookIMAPClient, create_outlook_imap_client_from_config

__all__ = [
    "GmailIMAPClient",
    "EmailMessage",
    "create_imap_client_from_config",
    "OutlookIMAPClient",
    "create_outlook_imap_client_from_config",
]

"""Authentication package"""
from src.auth.oauth2_gmail import OAuth2Gmail, create_oauth2_from_config
from src.auth.oauth2_outlook import OAuth2Outlook, create_outlook_oauth2_from_config

__all__ = [
    "OAuth2Gmail",
    "create_oauth2_from_config",
    "OAuth2Outlook",
    "create_outlook_oauth2_from_config",
]

"""Bounce detection and classification for email delivery failures."""

from __future__ import annotations

import re
from typing import Optional, Dict, Any, Tuple
from email.message import EmailMessage


def is_bounce_message(msg: EmailMessage) -> bool:
    """Detect if an email message is a bounce/delivery failure notification.
    
    Bounces are typically identified by:
    - Content-Type: multipart/report; report-type=delivery-status
    - From: MAILER-DAEMON or postmaster
    - Subject containing common bounce keywords
    """
    content_type = msg.get_content_type()
    
    # Check for delivery status report
    if content_type == 'multipart/report':
        params = msg.get_params()
        if params:
            for key, value in params:
                if key.lower() == 'report-type' and value.lower() == 'delivery-status':
                    return True
    
    # Check From address for common mailer daemon patterns
    from_header = msg.get('From', '').lower()
    bounce_senders = ['mailer-daemon', 'postmaster', 'mail delivery', 'noreply']
    if any(sender in from_header for sender in bounce_senders):
        return True
    
    # Check subject for bounce keywords
    subject = msg.get('Subject', '').lower()
    bounce_keywords = [
        'delivery failure', 'delivery status notification', 
        'undelivered mail', 'returned mail', 'mail delivery failed',
        'failure notice', 'delivery notification', 'undeliverable'
    ]
    if any(keyword in subject for keyword in bounce_keywords):
        return True
    
    return False


def extract_original_message_id(msg: EmailMessage) -> Optional[str]:
    """Extract the X-Genro-Mail-ID header from the original message.
    
    This ID correlates the bounce with the original sent message.
    Searches in the original message headers if present.
    """
    # First check if X-Genro-Mail-ID is in the bounce message itself
    genro_id = msg.get('X-Genro-Mail-ID')
    if genro_id:
        return genro_id.strip()
    
    # Look for the original message in multipart structure
    if msg.is_multipart():
        for part in msg.walk():
            # Check for message/rfc822 or text/rfc822-headers parts
            content_type = part.get_content_type()
            if content_type in ('message/rfc822', 'text/rfc822-headers'):
                # Parse the embedded message
                payload = part.get_payload()
                if isinstance(payload, list) and len(payload) > 0:
                    original_msg = payload[0]
                    if isinstance(original_msg, EmailMessage):
                        genro_id = original_msg.get('X-Genro-Mail-ID')
                        if genro_id:
                            return genro_id.strip()
    
    return None


def classify_bounce_type(msg: EmailMessage, body_text: str) -> Tuple[str, Optional[int]]:
    """Classify bounce as 'hard' (permanent) or 'soft' (temporary).
    
    Returns:
        tuple: (bounce_type, smtp_code)
            - bounce_type: 'hard' or 'soft'
            - smtp_code: extracted SMTP status code (e.g., 550, 421) or None
    """
    smtp_code = None
    
    # Look for SMTP status codes in the message
    # Common patterns: "550 ", "5.1.1", "421 ", "4.4.1"
    smtp_pattern = r'\b([2-5])\.?([0-9])\.?([0-9])\b|\b([2-5][0-9]{2})\b'
    
    # Search in subject
    subject = msg.get('Subject', '')
    match = re.search(smtp_pattern, subject)
    if match:
        if match.group(4):  # Three digit code
            smtp_code = int(match.group(4))
        elif match.group(1):  # X.Y.Z format
            smtp_code = int(match.group(1)) * 100 + int(match.group(2)) * 10 + int(match.group(3))
    
    # If not found, search in body
    if not smtp_code and body_text:
        match = re.search(smtp_pattern, body_text[:2000])  # Search first 2KB
        if match:
            if match.group(4):
                smtp_code = int(match.group(4))
            elif match.group(1):
                smtp_code = int(match.group(1)) * 100 + int(match.group(2)) * 10 + int(match.group(3))
    
    # Classify based on SMTP code
    if smtp_code:
        # 5xx codes are permanent failures
        if 500 <= smtp_code < 600:
            return 'hard', smtp_code
        # 4xx codes are temporary failures
        if 400 <= smtp_code < 500:
            return 'soft', smtp_code
    
    # Look for keywords in body text
    if body_text:
        body_lower = body_text.lower()
        
        # Hard bounce indicators
        hard_keywords = [
            'user unknown', 'no such user', 'recipient unknown',
            'mailbox not found', 'mailbox unavailable', 'address rejected',
            'does not exist', 'invalid recipient', 'unknown user',
            'permanent failure', 'permanently rejected'
        ]
        if any(keyword in body_lower for keyword in hard_keywords):
            return 'hard', smtp_code
        
        # Soft bounce indicators
        soft_keywords = [
            'mailbox full', 'quota exceeded', 'temporarily unavailable',
            'try again', 'temporary failure', 'greylisted',
            'connection timeout', 'service unavailable'
        ]
        if any(keyword in body_lower for keyword in soft_keywords):
            return 'soft', smtp_code
    
    # Default to soft bounce (safer for retry)
    return 'soft', smtp_code


def extract_failed_recipient(msg: EmailMessage, body_text: str) -> Optional[str]:
    """Extract the recipient email address that failed delivery."""
    # Look for X-Failed-Recipients header
    failed = msg.get('X-Failed-Recipients')
    if failed:
        return failed.strip()
    
    # Search body text for email patterns after common markers
    if body_text:
        # Common patterns in bounce messages
        patterns = [
            r'(?:to|for|recipient):\s*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
            r'<([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})>',
            r'\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, body_text[:2000], re.IGNORECASE)
            if match:
                return match.group(1)
    
    return None


def extract_bounce_reason(msg: EmailMessage, body_text: str) -> str:
    """Extract a human-readable reason for the bounce."""
    # Try to find diagnostic text in message
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == 'message/delivery-status':
                # Parse delivery status
                payload = part.get_payload()
                if isinstance(payload, list):
                    for status_part in payload:
                        if isinstance(status_part, EmailMessage):
                            diagnostic = status_part.get('Diagnostic-Code')
                            if diagnostic:
                                return diagnostic.strip()
                            action = status_part.get('Action')
                            if action:
                                return action.strip()
    
    # Fall back to subject or first line of body
    subject = msg.get('Subject', '')
    if subject and len(subject) > 10:
        return subject[:200]
    
    if body_text:
        # Get first meaningful line
        lines = body_text.strip().split('\n')
        for line in lines[:10]:
            line = line.strip()
            if len(line) > 10:
                return line[:200]
    
    return 'Delivery failed'


def parse_bounce_message(msg: EmailMessage, body_text: str) -> Optional[Dict[str, Any]]:
    """Parse a bounce message and extract all relevant information.
    
    Returns:
        Dictionary with bounce information or None if not a bounce
    """
    if not is_bounce_message(msg):
        return None
    
    original_id = extract_original_message_id(msg)
    bounce_type, smtp_code = classify_bounce_type(msg, body_text)
    failed_recipient = extract_failed_recipient(msg, body_text)
    reason = extract_bounce_reason(msg, body_text)
    
    return {
        'original_message_id': original_id,
        'bounce_type': bounce_type,
        'smtp_code': smtp_code,
        'failed_recipient': failed_recipient,
        'reason': reason
    }

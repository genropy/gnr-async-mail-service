"""Tests for bounce detection and classification."""

import pytest
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from async_mail_service.bounce_detection import (
    is_bounce_message,
    classify_bounce_type,
    extract_original_message_id,
    extract_failed_recipient,
    extract_bounce_reason,
    parse_bounce_message,
)


# Helper functions to create test messages


def create_message(
    from_addr="sender@example.com",
    subject="Test",
    body="Test body",
    content_type=None,
    headers=None,
):
    """Create a basic email message for testing."""
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["Subject"] = subject
    msg.set_content(body)

    if content_type:
        msg.replace_header("Content-Type", content_type)

    if headers:
        for key, value in headers.items():
            msg[key] = value

    return msg


def create_multipart_bounce(
    original_message_id=None,
    diagnostic_code=None,
    action=None,
    failed_recipient=None,
):
    """Create a realistic multipart bounce message."""
    msg = MIMEMultipart('report', report_type='delivery-status')
    msg["From"] = "MAILER-DAEMON@example.com"
    msg["Subject"] = "Delivery Status Notification (Failure)"

    if failed_recipient:
        msg["X-Failed-Recipients"] = failed_recipient

    # Part 1: Human readable explanation
    text_part = MIMEText("This is a bounce message")
    msg.attach(text_part)

    # Part 2: Delivery status (must be structured correctly for parsing)
    if diagnostic_code or action:
        status_container = EmailMessage()
        status_container.set_content("")
        status_container.replace_header("Content-Type", "message/delivery-status")

        # Create the actual status message as payload
        status_msg = EmailMessage()
        if diagnostic_code:
            status_msg["Diagnostic-Code"] = diagnostic_code
        if action:
            status_msg["Action"] = action

        # Set payload as a list containing the status message
        status_container.set_payload([status_msg])
        msg.attach(status_container)

    # Part 3: Original message
    if original_message_id:
        original = EmailMessage()
        original["X-Genro-Mail-ID"] = original_message_id
        original.set_content("Original message")

        rfc822_part = EmailMessage()
        rfc822_part.set_content("")
        rfc822_part.replace_header("Content-Type", "message/rfc822")
        rfc822_part.set_payload([original])
        msg.attach(rfc822_part)

    return msg


# Tests for is_bounce_message()


class TestIsBounceMessage:
    """Tests for bounce message detection."""

    def test_detect_by_content_type(self):
        """Should detect bounce by Content-Type: multipart/report."""
        msg = MIMEMultipart('report', report_type='delivery-status')
        msg["From"] = "sender@example.com"
        msg["Subject"] = "Test"

        assert is_bounce_message(msg) is True

    def test_detect_by_mailer_daemon_from(self):
        """Should detect bounce by MAILER-DAEMON in From header."""
        msg = create_message(from_addr="MAILER-DAEMON@example.com")
        assert is_bounce_message(msg) is True

    def test_detect_by_postmaster_from(self):
        """Should detect bounce by postmaster in From header."""
        msg = create_message(from_addr="postmaster@example.com")
        assert is_bounce_message(msg) is True

    def test_detect_by_mail_delivery_from(self):
        """Should detect bounce by 'mail delivery' in From header."""
        msg = create_message(from_addr="Mail Delivery System <noreply@example.com>")
        assert is_bounce_message(msg) is True

    def test_detect_by_subject_delivery_failure(self):
        """Should detect bounce by 'delivery failure' in subject."""
        msg = create_message(subject="Delivery Failure: Your message was not delivered")
        assert is_bounce_message(msg) is True

    def test_detect_by_subject_undelivered_mail(self):
        """Should detect bounce by 'undelivered mail' in subject."""
        msg = create_message(subject="Undelivered Mail Returned to Sender")
        assert is_bounce_message(msg) is True

    def test_detect_by_subject_case_insensitive(self):
        """Should detect bounce case-insensitively."""
        msg = create_message(subject="DELIVERY FAILURE")
        assert is_bounce_message(msg) is True

    def test_not_bounce_regular_message(self):
        """Should not detect regular message as bounce."""
        msg = create_message(
            from_addr="user@example.com",
            subject="Hello",
            body="This is a normal email"
        )
        assert is_bounce_message(msg) is False

    def test_not_bounce_partial_keyword(self):
        """Should not detect bounce if keyword is partial match."""
        msg = create_message(subject="I am delivering a package")
        assert is_bounce_message(msg) is False


# Tests for classify_bounce_type()


class TestClassifyBounceType:
    """Tests for bounce type classification."""

    def test_hard_bounce_5xx_in_subject(self):
        """Should classify as hard bounce when 5xx code in subject."""
        msg = create_message(subject="550 User not found")
        bounce_type, smtp_code = classify_bounce_type(msg, "")

        assert bounce_type == "hard"
        assert smtp_code == 550

    def test_hard_bounce_5xx_dotted_format(self):
        """Should classify as hard bounce when 5.x.x code in subject."""
        msg = create_message(subject="5.1.1 User unknown")
        bounce_type, smtp_code = classify_bounce_type(msg, "")

        assert bounce_type == "hard"
        assert smtp_code == 511

    def test_soft_bounce_4xx_in_subject(self):
        """Should classify as soft bounce when 4xx code in subject."""
        msg = create_message(subject="421 Service temporarily unavailable")
        bounce_type, smtp_code = classify_bounce_type(msg, "")

        assert bounce_type == "soft"
        assert smtp_code == 421

    def test_soft_bounce_4xx_dotted_format(self):
        """Should classify as soft bounce when 4.x.x code in body."""
        msg = create_message(subject="Delivery failure")
        body = "The server returned: 4.4.1 Connection timeout"
        bounce_type, smtp_code = classify_bounce_type(msg, body)

        assert bounce_type == "soft"
        assert smtp_code == 441

    def test_smtp_code_in_body(self):
        """Should extract SMTP code from body when not in subject."""
        msg = create_message(subject="Delivery failed")
        body = "Remote server returned error 550 User not found"
        bounce_type, smtp_code = classify_bounce_type(msg, body)

        assert bounce_type == "hard"
        assert smtp_code == 550

    def test_hard_bounce_by_keyword_user_unknown(self):
        """Should classify as hard bounce by 'user unknown' keyword."""
        msg = create_message(subject="Delivery failed")
        body = "The user unknown at this address"
        bounce_type, smtp_code = classify_bounce_type(msg, body)

        assert bounce_type == "hard"
        assert smtp_code is None

    def test_hard_bounce_by_keyword_no_such_user(self):
        """Should classify as hard bounce by 'no such user' keyword."""
        msg = create_message(subject="Delivery failed")
        body = "Error: no such user on this server"
        bounce_type, smtp_code = classify_bounce_type(msg, body)

        assert bounce_type == "hard"

    def test_soft_bounce_by_keyword_mailbox_full(self):
        """Should classify as soft bounce by 'mailbox full' keyword."""
        msg = create_message(subject="Delivery failed")
        body = "The recipient's mailbox full, try again later"
        bounce_type, smtp_code = classify_bounce_type(msg, body)

        assert bounce_type == "soft"
        assert smtp_code is None

    def test_soft_bounce_by_keyword_quota_exceeded(self):
        """Should classify as soft bounce by 'quota exceeded' keyword."""
        msg = create_message(subject="Delivery failed")
        body = "Quota exceeded for this mailbox"
        bounce_type, smtp_code = classify_bounce_type(msg, body)

        assert bounce_type == "soft"

    def test_default_to_soft_bounce(self):
        """Should default to soft bounce when no indicators found."""
        msg = create_message(subject="Delivery failed")
        body = "Something went wrong"
        bounce_type, smtp_code = classify_bounce_type(msg, body)

        assert bounce_type == "soft"
        assert smtp_code is None

    def test_smtp_code_priority_over_keywords(self):
        """Should prioritize SMTP code over keywords for classification."""
        msg = create_message(subject="550 Error")
        body = "mailbox full"  # Soft bounce keyword
        bounce_type, smtp_code = classify_bounce_type(msg, body)

        # Should be hard because of 550 code, not soft from keyword
        assert bounce_type == "hard"
        assert smtp_code == 550

    def test_search_limited_to_2kb(self):
        """Should only search first 2KB of body for SMTP code."""
        msg = create_message(subject="Delivery failed")
        # Create body with code after 2KB
        body = "x" * 2500 + " 550 User not found"
        bounce_type, smtp_code = classify_bounce_type(msg, body)

        # Should not find the code beyond 2KB
        assert smtp_code is None


# Tests for extract_original_message_id()


class TestExtractOriginalMessageId:
    """Tests for extracting original message ID."""

    def test_extract_from_bounce_header(self):
        """Should extract X-Genro-Mail-ID from bounce message header."""
        msg = create_message(headers={"X-Genro-Mail-ID": "test-id-123"})

        message_id = extract_original_message_id(msg)
        assert message_id == "test-id-123"

    def test_extract_from_embedded_message(self):
        """Should extract X-Genro-Mail-ID from embedded original message."""
        msg = create_multipart_bounce(original_message_id="embedded-id-456")

        message_id = extract_original_message_id(msg)
        assert message_id == "embedded-id-456"

    def test_strip_whitespace(self):
        """Should strip whitespace from extracted message ID."""
        msg = create_message(headers={"X-Genro-Mail-ID": "  test-id-789  "})

        message_id = extract_original_message_id(msg)
        assert message_id == "test-id-789"

    def test_return_none_when_not_found(self):
        """Should return None when X-Genro-Mail-ID not found."""
        msg = create_message()

        message_id = extract_original_message_id(msg)
        assert message_id is None

    def test_prefer_bounce_header_over_embedded(self):
        """Should prefer X-Genro-Mail-ID in bounce header over embedded."""
        msg = create_multipart_bounce(original_message_id="embedded-id")
        msg["X-Genro-Mail-ID"] = "bounce-header-id"

        message_id = extract_original_message_id(msg)
        assert message_id == "bounce-header-id"


# Tests for extract_failed_recipient()


class TestExtractFailedRecipient:
    """Tests for extracting failed recipient email."""

    def test_extract_from_header(self):
        """Should extract from X-Failed-Recipients header."""
        msg = create_message(headers={"X-Failed-Recipients": "user@example.com"})

        recipient = extract_failed_recipient(msg, "")
        assert recipient == "user@example.com"

    def test_extract_from_body_to_pattern(self):
        """Should extract email after 'to:' in body."""
        msg = create_message()
        body = "Failed delivery to: user@example.com"

        recipient = extract_failed_recipient(msg, body)
        assert recipient == "user@example.com"

    def test_extract_from_body_for_pattern(self):
        """Should extract email after 'for:' in body."""
        msg = create_message()
        body = "Message delivery failed for: recipient@example.com"

        recipient = extract_failed_recipient(msg, body)
        assert recipient == "recipient@example.com"

    def test_extract_from_body_angle_brackets(self):
        """Should extract email in angle brackets."""
        msg = create_message()
        body = "Delivery to <bounced@example.com> failed"

        recipient = extract_failed_recipient(msg, body)
        assert recipient == "bounced@example.com"

    def test_extract_from_body_bare_email(self):
        """Should extract bare email address from body."""
        msg = create_message()
        body = "Cannot deliver to failed.user@example.com due to error"

        recipient = extract_failed_recipient(msg, body)
        assert recipient == "failed.user@example.com"

    def test_case_insensitive_matching(self):
        """Should match patterns case-insensitively."""
        msg = create_message()
        body = "Failed delivery TO: user@example.com"

        recipient = extract_failed_recipient(msg, body)
        assert recipient == "user@example.com"

    def test_return_none_when_not_found(self):
        """Should return None when no email found."""
        msg = create_message()
        body = "Delivery failed but no email address here"

        recipient = extract_failed_recipient(msg, body)
        assert recipient is None

    def test_search_limited_to_2kb(self):
        """Should only search first 2KB of body."""
        msg = create_message()
        body = "x" * 2500 + " to: user@example.com"

        recipient = extract_failed_recipient(msg, body)
        assert recipient is None

    def test_strip_whitespace_from_header(self):
        """Should strip whitespace from header value."""
        msg = create_message(headers={"X-Failed-Recipients": "  user@example.com  "})

        recipient = extract_failed_recipient(msg, "")
        assert recipient == "user@example.com"


# Tests for extract_bounce_reason()


class TestExtractBounceReason:
    """Tests for extracting bounce reason."""

    def test_extract_from_diagnostic_code(self):
        """Should extract reason from Diagnostic-Code in delivery-status."""
        msg = create_multipart_bounce(
            diagnostic_code="smtp; 550 User not found"
        )

        reason = extract_bounce_reason(msg, "")
        assert reason == "smtp; 550 User not found"

    def test_extract_from_action(self):
        """Should extract reason from Action field when no Diagnostic-Code."""
        msg = create_multipart_bounce(action="failed")

        reason = extract_bounce_reason(msg, "")
        assert reason == "failed"

    def test_fallback_to_subject(self):
        """Should fallback to subject when no delivery-status parts."""
        msg = create_message(subject="Delivery failed: User mailbox full")

        reason = extract_bounce_reason(msg, "")
        assert reason == "Delivery failed: User mailbox full"

    def test_fallback_to_body_first_line(self):
        """Should fallback to first meaningful line of body."""
        msg = create_message(subject="Failed")  # Short subject
        body = "\n\nThis is the error message\nSecond line"

        reason = extract_bounce_reason(msg, body)
        assert reason == "This is the error message"

    def test_skip_short_body_lines(self):
        """Should skip lines shorter than 10 chars in body."""
        msg = create_message(subject="Err")  # Short subject
        body = "Hi\nShort\nThis is a longer error message"

        reason = extract_bounce_reason(msg, body)
        assert reason == "This is a longer error message"

    def test_truncate_long_reason(self):
        """Should truncate reason to 200 characters."""
        msg = create_message(subject="x" * 300)

        reason = extract_bounce_reason(msg, "")
        assert len(reason) == 200
        assert reason == "x" * 200

    def test_default_reason(self):
        """Should return 'Delivery failed' as default."""
        msg = create_message(subject="Err", body="")

        reason = extract_bounce_reason(msg, "")
        assert reason == "Delivery failed"

    def test_strip_whitespace_from_diagnostic_code(self):
        """Should strip whitespace from diagnostic code."""
        msg = create_multipart_bounce(diagnostic_code="  Error message  ")

        reason = extract_bounce_reason(msg, "")
        assert reason == "Error message"


# Tests for parse_bounce_message()


class TestParseBounceMessage:
    """Tests for complete bounce message parsing."""

    def test_parse_complete_bounce(self):
        """Should parse all fields from a complete bounce message."""
        msg = create_multipart_bounce(
            original_message_id="msg-123",
            diagnostic_code="smtp; 550 5.1.1 User not found",
            failed_recipient="user@example.com"
        )
        msg.replace_header("Subject", "550 Delivery failed")

        result = parse_bounce_message(msg, "User unknown error")

        assert result is not None
        assert result["original_message_id"] == "msg-123"
        assert result["bounce_type"] == "hard"
        assert result["smtp_code"] == 550
        assert result["failed_recipient"] == "user@example.com"
        assert "550" in result["reason"]

    def test_parse_soft_bounce(self):
        """Should parse soft bounce correctly."""
        msg = create_message(
            from_addr="MAILER-DAEMON@example.com",
            subject="421 Temporarily unavailable"
        )
        body = "Mailbox full, try again later. Recipient: user@example.com"

        result = parse_bounce_message(msg, body)

        assert result is not None
        assert result["bounce_type"] == "soft"
        assert result["smtp_code"] == 421
        assert result["failed_recipient"] == "user@example.com"

    def test_return_none_for_non_bounce(self):
        """Should return None for non-bounce messages."""
        msg = create_message(
            from_addr="user@example.com",
            subject="Hello",
            body="This is a normal email"
        )

        result = parse_bounce_message(msg, "Normal content")
        assert result is None

    def test_parse_with_missing_optional_fields(self):
        """Should handle missing optional fields gracefully."""
        msg = create_message(
            from_addr="MAILER-DAEMON@example.com",
            subject="Delivery failed"
        )
        body = "Unknown error occurred"

        result = parse_bounce_message(msg, body)

        assert result is not None
        assert result["original_message_id"] is None
        assert result["bounce_type"] == "soft"  # Default
        assert result["smtp_code"] is None
        assert result["failed_recipient"] is None
        assert result["reason"] == "Delivery failed"

    def test_integration_all_functions(self):
        """Integration test: verify all extraction functions are called."""
        msg = MIMEMultipart('report', report_type='delivery-status')
        msg["From"] = "MAILER-DAEMON@example.com"
        msg["Subject"] = "Delivery Status Notification (Failure)"
        msg["X-Genro-Mail-ID"] = "integration-test-id"
        msg["X-Failed-Recipients"] = "test@example.com"

        # Add human-readable part
        text_part = MIMEText("552 mailbox full")
        msg.attach(text_part)

        # Add delivery status part (correctly structured)
        status_container = EmailMessage()
        status_container.set_content("")
        status_container.replace_header("Content-Type", "message/delivery-status")

        status_msg = EmailMessage()
        status_msg["Diagnostic-Code"] = "smtp; 552 Quota exceeded"
        status_container.set_payload([status_msg])
        msg.attach(status_container)

        body = "552 mailbox full"
        result = parse_bounce_message(msg, body)

        assert result is not None
        assert result["original_message_id"] == "integration-test-id"
        assert result["bounce_type"] == "hard"
        assert result["smtp_code"] == 552
        assert result["failed_recipient"] == "test@example.com"
        assert "552" in result["reason"] or "Quota" in result["reason"]

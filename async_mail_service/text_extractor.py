"""Robust HTML to plain text extraction for email bodies."""

from typing import Optional
import html
import re
import logging

logger = logging.getLogger(__name__)


class TextExtractor:
    """Extract plain text from HTML email bodies with fallback strategies."""

    def extract(self, html_content: str, plain_fallback: Optional[str] = None) -> str:
        """
        Extract plain text from HTML with fallback strategy.

        Args:
            html_content: HTML email body
            plain_fallback: Native plain text part from email (if available)

        Returns:
            Clean plain text string
        """

        # If native plain text is available, use it directly (best case)
        if plain_fallback and plain_fallback.strip():
            return self._cleanup_text(plain_fallback)

        # Otherwise extract from HTML
        if not html_content or not html_content.strip():
            return ''

        # Try BeautifulSoup first (most robust)
        text = self._extract_beautifulsoup(html_content)
        if text:
            return text

        # Fallback to regex extraction
        return self._extract_regex(html_content)

    def _extract_beautifulsoup(self, html_content: str) -> str:
        """Extract using BeautifulSoup (handles malformed HTML)."""
        try:
            from bs4 import BeautifulSoup, Comment

            soup = BeautifulSoup(html_content, 'html.parser')

            # Remove non-content elements
            for element in soup(['script', 'style', 'head', 'meta', 'link', 'noscript']):
                element.decompose()

            # Remove HTML comments
            for comment in soup.find_all(text=lambda text: isinstance(text, Comment)):
                comment.extract()

            # Extract text with space separator
            text = soup.get_text(separator=' ', strip=True)

            # Decode HTML entities
            text = html.unescape(text)

            # Cleanup and return
            return self._cleanup_text(text)

        except ImportError:
            logger.debug("BeautifulSoup not available, falling back to regex")
            return ''
        except Exception as e:
            logger.debug(f"BeautifulSoup extraction failed: {e}")
            return ''

    def _extract_regex(self, html_content: str) -> str:
        """Extract using regex (fallback for extreme cases)."""
        try:
            # Remove script/style blocks
            text = re.sub(r'<script[^>]*?>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<style[^>]*?>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)

            # Remove HTML comments
            text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)

            # Remove all HTML tags
            text = re.sub(r'<[^>]+>', ' ', text)

            # Decode HTML entities
            text = html.unescape(text)

            return self._cleanup_text(text)

        except Exception as e:
            logger.warning(f"Regex extraction failed: {e}")
            return '[Text extraction failed]'

    def _cleanup_text(self, text: str) -> str:
        """Normalize and clean text."""
        # Remove null bytes
        text = text.replace('\x00', '')

        # Normalize whitespace
        text = re.sub(r' +', ' ', text)
        text = re.sub(r'\n\n+', '\n\n', text)

        # Remove common email artifacts
        text = re.sub(r'Sent from my iPhone.*$', '', text, flags=re.IGNORECASE | re.MULTILINE)
        text = re.sub(r'Get Outlook for .*$', '', text, flags=re.IGNORECASE | re.MULTILINE)

        return text.strip()

"""S3 storage for temporary email attachments."""

import base64
import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class S3AttachmentStorage:
    """Store email attachments in S3 with automatic cleanup."""

    def __init__(
        self,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        endpoint_url: Optional[str] = None,
        path_prefix: str = 'received/',
        inline_max_size: int = 512 * 1024
    ):
        """
        Initialize S3 attachment storage.

        Args:
            bucket: S3 bucket name
            region: AWS region (e.g., 'eu-west-1')
            access_key: AWS access key ID
            secret_key: AWS secret access key
            endpoint_url: Optional custom S3 endpoint (for MinIO)
            path_prefix: Prefix for S3 keys (default: 'received/')
            inline_max_size: Max size for inline base64 encoding (default: 512KB)
        """
        self.bucket = bucket
        self.region = region
        self.access_key = access_key
        self.secret_key = secret_key
        self.endpoint_url = endpoint_url
        self.path_prefix = path_prefix.rstrip('/') + '/' if path_prefix else ''
        self.inline_max_size = inline_max_size

        self._s3_client = None

    def _get_s3_client(self):
        """Lazy-load S3 client."""
        if self._s3_client is None:
            try:
                import boto3

                self._s3_client = boto3.client(
                    's3',
                    region_name=self.region,
                    aws_access_key_id=self.access_key,
                    aws_secret_access_key=self.secret_key,
                    endpoint_url=self.endpoint_url
                )
            except ImportError:
                raise RuntimeError("boto3 is required for S3 storage. Install with: pip install boto3")

        return self._s3_client

    async def store_attachments(self, message_id: str, attachments: List[Dict]) -> List[Dict]:
        """
        Store attachments for a message, returning references.

        Small attachments (<inline_max_size) are base64-encoded inline.
        Large attachments are uploaded to S3 with URL references.

        Args:
            message_id: Unique message identifier
            attachments: List of attachment dicts from mailparser

        Returns:
            List of attachment references with S3 URLs or inline data
        """
        if not attachments:
            return []

        results = []
        s3 = self._get_s3_client()

        for att in attachments:
            try:
                filename = att.get('filename', 'attachment')
                payload = att.get('payload', b'')
                content_type = att.get('content_type', 'application/octet-stream')

                if not payload:
                    logger.warning(f"Attachment {filename} has no payload, skipping")
                    continue

                # Sanitize filename
                safe_filename = self._sanitize_filename(filename)
                size = len(payload)

                # Small file: inline base64
                if size <= self.inline_max_size:
                    results.append({
                        'filename': filename,
                        'content_type': content_type,
                        'size': size,
                        'inline': base64.b64encode(payload).decode('utf-8')
                    })
                    logger.debug(f"Attachment {filename} ({size} bytes) stored inline")

                # Large file: upload to S3
                else:
                    s3_key = f"{self.path_prefix}{message_id}/{safe_filename}"

                    # Upload to S3
                    s3.put_object(
                        Bucket=self.bucket,
                        Key=s3_key,
                        Body=payload,
                        ContentType=content_type,
                        Metadata={
                            'message_id': message_id,
                            'original_filename': filename
                        }
                    )

                    results.append({
                        'filename': filename,
                        'content_type': content_type,
                        'size': size,
                        's3': {
                            'bucket': self.bucket,
                            'key': s3_key,
                            'region': self.region
                        }
                    })
                    logger.debug(f"Attachment {filename} ({size} bytes) uploaded to s3://{self.bucket}/{s3_key}")

            except Exception as e:
                logger.exception(f"Failed to store attachment {att.get('filename')}: {e}")
                continue

        return results

    async def cleanup_old_attachments(self, message_ids: List[str]) -> int:
        """
        Delete attachments for given message IDs from S3.

        Args:
            message_ids: List of message IDs to clean up

        Returns:
            Number of objects deleted
        """
        if not message_ids:
            return 0

        s3 = self._get_s3_client()
        deleted = 0

        for message_id in message_ids:
            try:
                # List all objects with this message_id prefix
                prefix = f"{self.path_prefix}{message_id}/"
                response = s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix)

                if 'Contents' not in response:
                    continue

                # Delete all objects
                objects = [{'Key': obj['Key']} for obj in response['Contents']]
                if objects:
                    s3.delete_objects(
                        Bucket=self.bucket,
                        Delete={'Objects': objects}
                    )
                    deleted += len(objects)
                    logger.debug(f"Deleted {len(objects)} attachments for message {message_id}")

            except Exception as e:
                logger.exception(f"Failed to cleanup attachments for message {message_id}: {e}")
                continue

        return deleted

    def _sanitize_filename(self, filename: str) -> str:
        """Remove unsafe characters from filename."""
        # Remove path separators and dangerous chars
        safe = re.sub(r'[^\w\s\.-]', '_', filename)
        # Limit length
        if len(safe) > 255:
            import os
            name, ext = os.path.splitext(safe)
            safe = name[:250] + ext
        return safe

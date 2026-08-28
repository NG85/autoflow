import base64
import hashlib
import hmac
import json
import logging
import re
import urllib.parse
from datetime import UTC, datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

def _sign(key, msg):
    return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

def _get_tos_signature_key(key, dateStamp, regionName, serviceName):
    kDate = _sign(key.encode('utf-8'), dateStamp)
    kRegion = _sign(kDate, regionName)
    kService = _sign(kRegion, serviceName)
    kSigning = _sign(kService, 'request')
    return kSigning

def _get_tos_sign_headers(method, service, host, region, request_parameters, access_key, secret_key):
    contenttype = 'application/x-www-form-urlencoded'
    accept = 'application/json'
    t = datetime.now(UTC)
    xdate = t.strftime('%Y%m%dT%H%M%SZ')
    datestamp = t.strftime('%Y%m%d')
   
    # 1. Canonical Request
    canonical_uri = '/'
    canonical_querystring = request_parameters
    canonical_headers = 'content-type:'+ contenttype + '\n' +'host:' + host + '\n' + 'x-date:' + xdate + '\n'
    signed_headers = 'content-type;host;x-date'
    payload_hash = hashlib.sha256(('').encode('utf-8')).hexdigest()
    canonical_request = method + '\n' + canonical_uri + '\n' + canonical_querystring + '\n' + canonical_headers + '\n' + signed_headers + '\n' + payload_hash
    
    # 2. Credential String
    algorithm = 'HMAC-SHA256'
    credential_scope = datestamp + '/' + region + '/' + service + '/' + 'request'
    string_to_sign = algorithm + '\n' +  xdate + '\n' +  credential_scope + '\n' +  hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()
    
    # 3. Signing Key
    signing_key = _get_tos_signature_key(secret_key, datestamp, region, service)
    signature = hmac.new(signing_key, (string_to_sign).encode('utf-8'), hashlib.sha256).hexdigest()
    
    # 4. Authorization Header
    authorization_header = algorithm + ' ' + 'Credential=' + access_key + '/' + credential_scope + ', ' +  'SignedHeaders=' + signed_headers + ', ' + 'Signature=' + signature
    headers = {'Accpet':accept, 'Content-Type':contenttype, 'X-Date':xdate, 'Authorization':authorization_header}
    return headers

def get_tos_sts_token(host: str, region: str, access_key: str, secret_key: str):
    # Send request to get temporary AK/SK+Token
    method = 'GET'
    service = 'sts'
    endpoint = f"https://{host}"
    query_parameters = {
        'Action': 'AssumeRole',
        'RoleSessionName': 'tos_role_session',
        'RoleTrn': 'trn:iam::2103251870:role/tos_role',
        'Version': '2018-01-01'
    }
    request_parameters = urllib.parse.urlencode(query_parameters)
    headers = _get_tos_sign_headers(method, service, host, region, request_parameters, access_key, secret_key)
    request_url = endpoint + '?' + request_parameters
    r = requests.get(request_url, headers=headers)
    return r.text

# MinIO POST uses SigV2 HMAC-SHA1 (fixed MinIO build). UFile rejects V2 and requires SigV4.
AWS4_ALGORITHM = "AWS4-HMAC-SHA256"
AWS4_SERVICE = "s3"
AWS4_REQUEST = "aws4_request"
DEFAULT_S3_REGION = "us-east-1"
DEFAULT_POST_EXPIRY_SECONDS = 3600
MAX_POST_UPLOAD_BYTES = 104857600  # 100MB
_UFILE_REGION_RE = re.compile(r"s3-([a-z0-9-]+)\.ufileos\.com$", re.IGNORECASE)


def infer_s3_region(endpoint: str, explicit_region: Optional[str] = None) -> str:
    """Resolve region for SigV4. Prefer explicit config, then UFile host, else us-east-1."""
    if explicit_region:
        return explicit_region
    host = endpoint or ""
    if "://" not in host:
        host = f"//{host}"
    parsed_host = (urlparse(host).hostname or "").lower()
    match = _UFILE_REGION_RE.search(parsed_host)
    if match:
        return match.group(1)
    return DEFAULT_S3_REGION


def _s3_v4_signing_key(secret_key: str, datestamp: str, region: str) -> bytes:
    k_date = hmac.new(("AWS4" + secret_key).encode("utf-8"), datestamp.encode("utf-8"), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode("utf-8"), hashlib.sha256).digest()
    k_service = hmac.new(k_region, AWS4_SERVICE.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(k_service, AWS4_REQUEST.encode("utf-8"), hashlib.sha256).digest()


def _sign_s3v4_post_policy(policy_b64: str, secret_key: str, datestamp: str, region: str) -> str:
    signing_key = _s3_v4_signing_key(secret_key, datestamp, region)
    return hmac.new(signing_key, policy_b64.encode("utf-8"), hashlib.sha256).hexdigest()


def _generate_s3v4_post_policy(
    bucket: str,
    access_key: str,
    secret_key: str,
    region: str,
    expiry_seconds: int = DEFAULT_POST_EXPIRY_SECONDS,
    now: Optional[datetime] = None,
) -> tuple[str, str, str, str, str]:
    """Build a SigV4 POST policy. Returns policy_b64, credential, amz_date, signature, expiration."""
    now = now or datetime.now(UTC)
    expiration = (now + timedelta(seconds=expiry_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    datestamp = now.strftime("%Y%m%d")
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    credential = f"{access_key}/{datestamp}/{region}/{AWS4_SERVICE}/{AWS4_REQUEST}"
    policy_doc = {
        "expiration": expiration,
        "conditions": [
            {"bucket": bucket},
            ["starts-with", "$key", ""],
            {"success_action_status": "201"},
            ["content-length-range", 0, MAX_POST_UPLOAD_BYTES],
            {"x-amz-algorithm": AWS4_ALGORITHM},
            {"x-amz-credential": credential},
            {"x-amz-date": amz_date},
        ],
    }
    policy_b64 = base64.b64encode(
        json.dumps(policy_doc, separators=(",", ":")).encode("utf-8")
    ).decode("utf-8")
    signature = _sign_s3v4_post_policy(policy_b64, secret_key, datestamp, region)
    return policy_b64, credential, amz_date, signature, expiration


def _generate_minio_policy(bucket: str, expiry_seconds: int = 3600) -> str:
    """生成 MinIO 策略文档（SigV2）。"""
    policy = {
        "expiration": (datetime.now(UTC) + timedelta(seconds=expiry_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "conditions": [
            {"bucket": bucket},
            ["starts-with", "$key", ""],
            {"success_action_status": "201"},
            ["content-length-range", 0, MAX_POST_UPLOAD_BYTES],
        ],
    }
    return base64.b64encode(json.dumps(policy).encode()).decode()


def _generate_minio_signature(policy: str, secret_key: str) -> str:
    """生成 MinIO SigV2 签名（HMAC-SHA1）。"""
    signature = hmac.new(
        secret_key.encode(),
        policy.encode(),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(signature).decode()


def get_minio_sts_token(access_key: str, secret_key: str, bucket: str, endpoint: str) -> dict:
    """获取 MinIO 的临时凭证（SigV2 POST policy + HMAC-SHA1）。"""
    try:
        policy = _generate_minio_policy(bucket)
        signature = _generate_minio_signature(policy, secret_key)
        credentials = {
            "access_key_id": access_key,
            "secret_access_key": secret_key,
            "session_token": f"{policy}:{signature}",
            "expiration": (datetime.now(UTC) + timedelta(seconds=DEFAULT_POST_EXPIRY_SECONDS)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }
        return {"Result": {"Credentials": credentials}}
    except Exception as e:
        logger.error(f"Failed to get MinIO STS token: {e}")
        raise


def get_ufile_sts_token(
    access_key: str,
    secret_key: str,
    bucket: str,
    endpoint: str,
    region: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict:
    """
    Issue UFile POST credentials (AWS Signature Version 4).

    Frontend must POST ``post_form`` fields (not SigV2 AWSAccessKeyId + HMAC-SHA1).
    """
    try:
        now = now or datetime.now(UTC)
        resolved_region = infer_s3_region(endpoint, region)
        policy, credential, amz_date, signature, expiration = _generate_s3v4_post_policy(
            bucket=bucket,
            access_key=access_key,
            secret_key=secret_key,
            region=resolved_region,
            now=now,
        )
        post_form = {
            "policy": policy,
            "success_action_status": "201",
            "x-amz-algorithm": AWS4_ALGORITHM,
            "x-amz-credential": credential,
            "x-amz-date": amz_date,
            "x-amz-signature": signature,
        }
        credentials = {
            "access_key_id": access_key,
            "secret_access_key": secret_key,
            "session_token": f"{policy}:{signature}",
            "expiration": expiration,
            "region": resolved_region,
            "signature_version": "s3v4",
            "post_form": post_form,
        }
        return {"Result": {"Credentials": credentials}}
    except Exception as e:
        logger.error(f"Failed to get UFile STS token: {e}")
        raise

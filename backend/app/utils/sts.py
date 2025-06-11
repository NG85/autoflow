import base64
import datetime, hashlib, hmac
import json
import urllib.parse
import requests, urllib
import logging
from datetime import UTC, datetime, timedelta

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

def _generate_minio_policy(bucket: str, expiry_seconds: int = 3600) -> str:
    """生成 MinIO 策略文档"""
    policy = {
        "expiration": (datetime.now(UTC) + timedelta(seconds=expiry_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "conditions": [
            {"bucket": bucket},
            ["starts-with", "$key", ""],
            {"success_action_status": "201"},
            ["content-length-range", 0, 104857600]  # 最大100MB
        ]
    }
    return base64.b64encode(json.dumps(policy).encode()).decode()

def _generate_minio_signature(policy: str, secret_key: str) -> str:
    """生成 MinIO 签名"""
    signature = hmac.new(
        secret_key.encode(),
        policy.encode(),
        hashlib.sha1
    ).digest()
    return base64.b64encode(signature).decode()

def get_minio_sts_token(access_key: str, secret_key: str, bucket: str, endpoint: str) -> dict:
    """
    获取 MinIO 的临时凭证
    
    Args:
        access_key: MinIO 访问密钥
        secret_key: MinIO 密钥
        bucket: MinIO 存储桶名称
        endpoint: MinIO 服务端点
        
    Returns:
        包含临时凭证的字典
    """
    try:
        # 生成策略和签名
        policy = _generate_minio_policy(bucket)
        signature = _generate_minio_signature(policy, secret_key)
        
        # 生成临时凭证
        credentials = {
            "access_key_id": access_key,
            "secret_access_key": secret_key,
            "session_token": f"{policy}:{signature}",
            "expiration": (datetime.now(UTC) + timedelta(seconds=3600)).strftime("%Y-%m-%dT%H:%M:%SZ")
        }
        
        return {
            "Result": {
                "Credentials": credentials
            }
        }
    except Exception as e:
        logger.error(f"Failed to get MinIO STS token: {e}")
        raise
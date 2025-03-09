import datetime, hashlib, hmac
import urllib.parse
import requests, urllib
import logging

logger = logging.getLogger(__name__)

def sign(key, msg):
    return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

def getSignatureKey(key, dateStamp, regionName, serviceName):
    kDate = sign(key.encode('utf-8'), dateStamp)
    kRegion = sign(kDate, regionName)
    kService = sign(kRegion, serviceName)
    kSigning = sign(kService, 'request')
    return kSigning

def getSignHeaders(method, service, host, region, request_parameters, access_key, secret_key):
    contenttype = 'application/x-www-form-urlencoded'
    accept = 'application/json'
    t = datetime.datetime.now(datetime.timezone.utc)
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
    signing_key = getSignatureKey(secret_key, datestamp, region, service)
    signature = hmac.new(signing_key, (string_to_sign).encode('utf-8'), hashlib.sha256).hexdigest()
    
    # 4. Authorization Header
    authorization_header = algorithm + ' ' + 'Credential=' + access_key + '/' + credential_scope + ', ' +  'SignedHeaders=' + signed_headers + ', ' + 'Signature=' + signature
    headers = {'Accpet':accept, 'Content-Type':contenttype, 'X-Date':xdate, 'Authorization':authorization_header}
    return headers

def get_sts_token(host: str, region: str, access_key: str, secret_key: str):
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
    headers = getSignHeaders(method, service, host, region, request_parameters, access_key, secret_key)
    request_url = endpoint + '?' + request_parameters
    r = requests.get(request_url, headers=headers)
    return r.text
"""MinIO STS stays SigV2; UFile STS issues AWS Signature Version 4."""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

from app.utils import sts


def _v4_signing_key(secret: str, datestamp: str, region: str) -> bytes:
    k_date = hmac.new(("AWS4" + secret).encode(), datestamp.encode(), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode(), hashlib.sha256).digest()
    k_service = hmac.new(k_region, b"s3", hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()


def test_infer_region_from_ufile_endpoint():
    assert sts.infer_s3_region("s3-cn-wlcb.ufileos.com") == "cn-wlcb"
    assert sts.infer_s3_region("https://s3-cn-wlcb.ufileos.com") == "cn-wlcb"
    assert sts.infer_s3_region("https://aptsell.s3-cn-wlcb.ufileos.com") == "cn-wlcb"
    assert sts.infer_s3_region("localhost:9000") == "us-east-1"
    assert sts.infer_s3_region("s3-cn-wlcb.ufileos.com", explicit_region="cn-bj") == "cn-bj"


def test_minio_sts_keeps_hmac_sha1_v2():
    result = sts.get_minio_sts_token(
        access_key="minioadmin",
        secret_key="minioadmin",
        bucket="autoflow",
        endpoint="localhost:9000",
    )
    creds = result["Result"]["Credentials"]
    policy, signature = creds["session_token"].split(":", 1)
    expected = base64.b64encode(
        hmac.new(b"minioadmin", policy.encode(), hashlib.sha1).digest()
    ).decode()
    assert signature == expected
    assert "signature_version" not in creds
    assert "post_form" not in creds
    decoded = json.loads(base64.b64decode(policy))
    condition_keys = {
        key
        for item in decoded["conditions"]
        if isinstance(item, dict)
        for key in item
    }
    assert "x-amz-algorithm" not in condition_keys


def test_ufile_sts_issues_s3v4_post_form():
    now = datetime(2026, 8, 28, 8, 44, 0, tzinfo=UTC)
    result = sts.get_ufile_sts_token(
        access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        bucket="aptsell",
        endpoint="s3-cn-wlcb.ufileos.com",
        now=now,
    )
    creds = result["Result"]["Credentials"]
    post_form = creds["post_form"]

    assert creds["region"] == "cn-wlcb"
    assert creds["signature_version"] == "s3v4"
    assert post_form["x-amz-algorithm"] == "AWS4-HMAC-SHA256"
    assert post_form["x-amz-credential"] == (
        "AKIDEXAMPLE/20260828/cn-wlcb/s3/aws4_request"
    )
    assert post_form["x-amz-date"] == "20260828T084400Z"
    assert post_form["success_action_status"] == "201"

    signature = post_form["x-amz-signature"]
    assert len(signature) == 64
    assert all(c in "0123456789abcdef" for c in signature)

    policy = json.loads(base64.b64decode(post_form["policy"]))
    condition_keys = {
        key
        for item in policy["conditions"]
        if isinstance(item, dict)
        for key in item
    }
    assert "x-amz-algorithm" in condition_keys
    assert "x-amz-credential" in condition_keys
    assert "x-amz-date" in condition_keys

    expected = hmac.new(
        _v4_signing_key("wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "20260828", "cn-wlcb"),
        post_form["policy"].encode(),
        hashlib.sha256,
    ).hexdigest()
    assert signature == expected
    assert creds["session_token"] == f"{post_form['policy']}:{signature}"

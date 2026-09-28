"""Create RDS via CloudFormation, then configure a restricted sync role.

No credentials are accepted on the command line or written to the repository.
AWS CLI performs authentication using the chosen local profile.
"""
import argparse
import ipaddress
import json
import os
import ssl
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tracker.config import DATA_DIR


def main():
    parser = argparse.ArgumentParser(description="Create and connect an AWS RDS PostgreSQL database (billable AWS resources).")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--stack", default="computer-typology")
    parser.add_argument("--allow-cidr", required=True, help="Your public IPv4 address/32; never 0.0.0.0/0")
    parser.add_argument("--connect-existing", action="store_true", help="Skip stack creation and configure an existing completed stack")
    parser.add_argument("--validate-only", action="store_true", help="Ask AWS to validate the template; create nothing")
    args = parser.parse_args()
    network = ipaddress.ip_network(args.allow_cidr, strict=True)
    if network.version != 4 or network.prefixlen != 32 or not network.network_address.is_global:
        parser.error("--allow-cidr must be a single public IPv4 address followed by /32")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # AWS CLI's bundled CA file may omit a local enterprise root. Use the
    # Windows trust store, retaining TLS verification instead of bypassing it.
    aws_ca = DATA_DIR / "aws-cli-ca.pem"
    aws_ca.write_text("".join(ssl.DER_cert_to_PEM_cert(c) for c in ssl.create_default_context().get_ca_certs(binary_form=True)), encoding="ascii")

    child_env = os.environ.copy()
    child_env.update(AWS_PROFILE=args.profile, AWS_REGION=args.region, AWS_CA_BUNDLE=str(aws_ca), SSL_CERT_FILE=str(aws_ca))

    def aws(*arguments):
        command = ["aws", "--profile", args.profile, "--region", args.region, "--ca-bundle", str(aws_ca), "--output", "json", "--no-cli-pager", *arguments]
        result = subprocess.run(command, capture_output=True, text=True, env=child_env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        return json.loads(result.stdout) if result.stdout.strip() else {}

    aws("sts", "get-caller-identity")
    template = Path(__file__).resolve().parent.parent / "infra" / "aws-postgres.json"
    aws("cloudformation", "validate-template", "--template-body", "file://" + str(template))
    if args.validate_only:
        print("AWS accepted the template. No resources were created.")
        return
    if not args.connect_existing:
        versions = aws("rds", "describe-db-engine-versions", "--engine", "postgres")["DBEngineVersions"]
        supported = [v["EngineVersion"] for v in versions if v.get("DBParameterGroupFamily") == "postgres18" and v.get("Status") == "available"]
        if not supported:
            raise RuntimeError("No available PostgreSQL 18 version was returned for this region")
        version = max(supported, key=lambda value: tuple(int(part) for part in value.split('.')))
        orderable = aws("rds", "describe-orderable-db-instance-options", "--engine", "postgres", "--engine-version", version, "--db-instance-class", "db.t4g.micro")
        if not orderable["OrderableDBInstanceOptions"]:
            raise RuntimeError("The selected region does not offer db.t4g.micro with PostgreSQL " + version)
        print("Creating billable RDS, storage, backups, and a managed secret. Provisioning usually takes several minutes.", flush=True)
        aws("cloudformation", "create-stack", "--stack-name", args.stack, "--template-body", "file://" + str(template), "--parameters", f"ParameterKey=AllowedClientCidr,ParameterValue={args.allow_cidr}", f"ParameterKey=PostgreSQLVersion,ParameterValue={version}")
        last_status = None
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            status = aws("cloudformation", "describe-stacks", "--stack-name", args.stack)["Stacks"][0]["StackStatus"]
            if status != last_status:
                print("AWS stack: " + status, flush=True)
                last_status = status
            if status == "CREATE_COMPLETE":
                break
            if status != "CREATE_IN_PROGRESS":
                raise RuntimeError("AWS did not finish creating the stack. Inspect CloudFormation events before retrying: " + status)
            time.sleep(20)
        else:
            raise RuntimeError("Timed out waiting for AWS. Resources may still be provisioning; inspect the stack, then use --connect-existing.")
    stack = aws("cloudformation", "describe-stacks", "--stack-name", args.stack)["Stacks"][0]
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
    secret_arn = outputs["MasterSecretArn"]
    child_env["TYPOLOGY_ADMIN_USER"] = "typology_admin"  # Public template parameter, not a secret.
    child_env["TYPOLOGY_ADMIN_PASSWORD"] = "{{resolve:secretsmanager:" + secret_arn + ":SecretString:password}}"
    child_env["ASM_EXEC_MCP_TIMEOUT"] = "60"
    scripts = Path(__file__).resolve().parent
    result = subprocess.run([sys.executable, str(scripts / "vendor" / "asm-exec"), "--",
                             sys.executable, str(scripts / "bootstrap_postgres.py"),
                             "--host", outputs["Endpoint"], "--port", outputs["Port"],
                             "--database", outputs["DatabaseName"]], env=child_env)
    if result.returncode:
        raise RuntimeError("Database bootstrap did not complete. The AWS stack is preserved; retry with --connect-existing.")
    print("PostgreSQL is ready. The recorder catches up within one minute, then uploads daily.")
    print("If your public IP changes, update AllowedClientCidr on the CloudFormation stack before reconnecting.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"AWS setup stopped ({type(error).__name__}). {error}", file=sys.stderr)
        raise SystemExit(1)

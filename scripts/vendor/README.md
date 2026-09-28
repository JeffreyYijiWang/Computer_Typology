# AWS secret resolver

`asm-exec` is a copy of the AWS Agent Toolkit runtime resolver,
retrieved on 2026-09-28 from:
https://github.com/aws/agent-toolkit-for-aws/blob/main/plugins/aws-core/skills/aws-secrets-manager/references/asm-exec

It resolves Secrets Manager references inside the child environment, keeping
administrator passwords out of the console and agent context. The original
Apache 2.0 license is included in `LICENSE-aws-agent-toolkit`.

Local adaptation: retry up to four attempts on connection resets/transport
failures during the read-only MCP resolver requests. HTTP authentication and
authorization errors are not retried. Secret resolution and redaction behavior
are unchanged.

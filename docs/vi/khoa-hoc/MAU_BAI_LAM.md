# Mẫu nhật ký và bài làm

Sao chép các mẫu dưới đây cho từng bài. Không sửa file mẫu gốc.

## 1. Nhật ký lab

```text
Bài / Lab:
Ngày:
Commit:
Python environment:
Data manifest/window:
Mục tiêu:
Exact command:
Output directory:
Exit code:
Warnings:
Artifact IDs:
Kết quả quan sát:
Kết luận:
Điều chưa chứng minh được:
```

## 2. Code-reading trace

```text
Input contract:
Entry point:
Module/class/function đi qua:
State được đọc:
State được ghi:
Output contract:
Exception/fail-closed path:
Test bảo vệ:
Artifact/audit evidence:
```

## 3. Review strategy

```text
Strategy ID:
Descriptor ID:
Required features:
Warmup:
Parameter schema:
Signal convention:
NO_TRADE conditions:
State scope:
Determinism evidence:
Legacy/canonical parity claim:
Known risks:
```

## 4. Audit report/artifact

```text
Schema version:
Artifact ID:
Commit/code identity:
Data identity:
Strategy/descriptor:
Params hash:
Cost/fault scenario:
Evaluation window:
Trade/effective sample:
Return + benchmark:
Drawdown + tail risk:
Execution costs:
Reject/partial/fault health:
Warnings/failures:
Lineage parents:
```

## 5. Selection memo

```text
Candidate set:
Incumbent:
Locked policy:
Train/validation/test split:
Multiple-testing treatment:
Hard gates:
Robustness checks:
Eligible candidates:
Uncertainty:
Decision: SELECT | KEEP_INCUMBENT | NO_SELECTION
Reason:
What would change the decision:
```

## 6. Promotion/release record

```text
Environment:
Selection policy ID:
Promotion record ID:
Artifact lineage verified:
Risk budget:
Expiry/revocation:
Rollback target:
Shadow/paper/testnet evidence:
Reconciliation status:
Operator/reviewer:
Decision: APPROVE | REJECT | CONDITIONAL
```

## 7. Incident exercise

```text
Trigger:
Detection source:
Potential exposure:
Immediate safe action:
Evidence preserved:
Reconciliation needed:
Rollback/recovery:
Escalation:
Root cause hypothesis:
Proof required before resume:
```


# Ghi chú phiên đã đưa khỏi thư mục gốc

Các file ở đây từng nằm ngay thư mục gốc của repo. Đó là handover, audit và bản tóm tắt theo từng phiên làm việc.

Trạng thái đang dùng nằm ở [`docs/CAMPAIGN_STATUS.md`](../../CAMPAIGN_STATUS.md).

Ba tài liệu vẫn ở gốc vì script và test gọi đúng tên file:

- `POLICY_RETURN_AUDIT.md`
- `GATE_SEMANTICS_AND_RERANK.md`
- `CAMPAIGN_REPRODUCIBILITY_ROOT_CAUSE.md`

Output JSON/log và các `*_TODO.md` không được git theo dõi nằm ở `archive/session-notes/` và `archive/scratch-outputs/` (gitignore). Gói trong `data/` không bị di chuyển.

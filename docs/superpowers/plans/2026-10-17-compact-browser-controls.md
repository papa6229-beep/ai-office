# Compact browser controls

Existing production observations contain the keyword input and search link but
return about 58,600 characters. The user reports a Claude-side token error;
the office ledger records success, so these are different delivery boundaries.
Official Claude documentation describes a separate 50,000-character persistence
threshold: https://code.claude.com/docs/en/mcp#mcp-output-limits-and-warnings .
The CLI has no general file-reading tool in this office adapter, so the original
58,600-character result cannot be treated as directly delivered model context.

- Keep observed element handles, signatures, secret-value protection and the
  current browser/session. No site-specific selectors or inferred URL actions.
- Inspect controls without collecting the full page body. Prioritize editable
  fields and actions before dropdowns, toggles and navigation links across frames.
- Add a literal optional query over observed labels/names and option labels.
  Compact public metadata to a byte budget, preserving actual control refs.
  Expose option truncation and allow selecting only options actually returned.
- Keep browser_read/export for page text and browser_value for ordinary values.
- Explain the small observation workflow to every model. Syntax/read-only size
  measurements only; no tests or paid calls. Back up and activate at an idle point.
- The user checks actual search behavior. A small result does not prove model
  reasoning or site submission success.

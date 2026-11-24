# Event product links implementation plan

The approved outcome is to add new-window product links to the existing December event on PC and mobile, and provide a reusable automatic-link button in the publisher.

## Design

Keep uploaded images intact. Store link rectangles in image-relative coordinates with the image SHA256. Windows local OCR proposes product text and button locations. The existing authenticated Ego/BizHost product search supplies current visible, selling products, prices and product IDs. Ambiguous matches remain selectable in the preview. Publishing rechecks image identity, product identity and the editor source, backs up both skins and reconciles partial saves through the existing publisher journal. Every link has an accessible product label and opens the corresponding device's product page in a new tab.

## Work

- [x] Add local OCR with word positions and an automatic product-search worker.
- [x] Add persistent analysis/publication jobs and source-preserving link rendering for both skins.
- [x] Add an automatic-link button, image/region preview, candidate choices and publication status.
- [x] Apply the current 17-product mapping through the reusable workflow.
- [x] Verify public HTML for both devices, desktop click targets and new-tab behavior. The mobile page at 390 CSS pixels contains the 17 mobile destinations and in-image rectangles; its existing age-verification gate prevents a full visual/click check in this browser. No tests were added or run.
- [x] Activate the publisher on port 8877 and execute analysis, candidate selection and publication through the actual button. OCR selected 15 matches; two were reviewed against the confirmed products. Source receipts and task-specific Git synchronization are recorded in the work journal.

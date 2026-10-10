# Route arrow template

`route_arrow.png` is a monochrome silhouette reconstructed from the supplied
chevron-shaped arrow reference. It is not an exact pixel crop of the attachment.
White pixels describe the arrow shape and black pixels describe the background.

Movement and Space checks use grayscale edges and normalized image matching.
36 rotated templates cover 360 degrees. Candidate crops are resized to handle
scale changes. No arrow colour threshold is used in this mode.

Matching searches near the configured character position and excludes the HUD.
Scores below 0.65 are rejected. Contrast and visible shape are still necessary;
heavy occlusion or a visually similar object may affect detection.

For an exact reference, replace this PNG with a clean monochrome silhouette,
centered with enough padding to rotate without clipping, then restart GameBot.
Detected orientation is not used to steer: the character-to-marker vector and
the existing planned heading determine movement.

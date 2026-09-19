# Welcome layout

The welcome screen uses the project's Unicode wolf artwork. On a wide
terminal, the right panel begins at column 45: title on row 5, hostname on row 8,
worker summary on row 11, and commands on rows 13–16. Version, hostname and
worker state remain live values. The complete reference status line needs at
least 101 columns; a longer hostname or larger counts may need more width.


At 80 columns the status wraps without moving the title, hostname or status
anchor. Commands move down only as needed to avoid the wrapped status.


Linux consoles and basic serial terminals retain the ASCII wolf fallback.
Small terminals and redirected output use a compact layout, with wrapped
worker counts when space permits. All output fits the available dimensions;
very small windows show only the fields that fit. No cursor movement or
screen-clearing sequences are emitted.



Public screenshots must be captured with synthetic fixture values and reviewed
for private information. Source-rendered previews must be labelled as previews,
not live SSH screenshots. Login and shell components own password prompts,
last-login records and shell prompts.

The presentation tests compare the entire 101-column output to the supplied
reference, with dynamic version and hostname fields. They also check fixed
anchors while wrapping, live field substitution, narrow widths/heights,
console fallback, terminal sanitization and matching color resets.

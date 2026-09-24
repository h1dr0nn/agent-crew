# Recipes

## A new function with tests

~~~markdown
@@template rules

# Task B2: `palette::nearest` - map a colour to the nearest palette index

The importer (task B3) needs the nearest palette entry for each pixel.

## Files you own
- `src/palette/nearest.rs` (new)
- `src/palette/mod.rs` (add `pub mod nearest;` only)

## What to write
```rust
pub fn nearest(palette: &[Rgba], colour: Rgba) -> Option<u8>
```
- Distance is squared RGB distance; alpha is ignored.
- Ties go to the lower index.
- An empty palette returns `None`.
- A palette longer than 256 entries: only the first 256 count.

## Tests
In `nearest.rs`: exact hit, a tie, empty palette, 300 entries.

Verify kind: `rust`

## Inlined sources
@@grep src/palette/mod.rs ^pub
@@include src/palette/types.rs#L1-L40
~~~

## A fix from a review finding

~~~markdown
@@template rules

# Task B2-fix: ties in `nearest` go to the higher index; they must go to the lower

In `src/palette/nearest.rs` line 14, `<=` must be `<`. Add a test
`ties_prefer_the_lower_index` with palette [red, red] and colour red,
expecting `Some(0)`.

## Files you own
- `src/palette/nearest.rs`

Verify kind: `rust`

@@include src/palette/nearest.rs
~~~

## A cross-model review

~~~markdown
@@template task-review

## The spec
@@include docs/plan/PHASE-3.md#L40-L75

@@diff main src/palette
~~~

Run with `--role reviewer`, no `--owns`.

# Clearing
Removing debris: clear and its kinds, the tools big debris needs, and the one-swing chop and break_rocks.

## clear
`clear(area, kinds=["weeds", "twigs", "stones"])`

Clears the chosen kinds of debris inside the area (`[x1, y1, x2, y2]`, inclusive; an area is required), taking the right tool for each and swinging until each is gone, nearest first.

Kinds:
| kind | what | tool |
|---|---|---|
| `"weeds"` | weeds | scythe |
| `"twigs"` | twigs | axe |
| `"stones"` | small stones | pickaxe |
| `"trees"` | trees, and the small stumps they leave | axe |
| `"saplings"` | young trees | axe |
| `"grass"` | long grass | scythe |
| `"dead_crops"` | dead crops | scythe |
| `"stumps"` | large stumps (copper axe or better) and large logs (steel axe or better) | axe |
| `"boulders"` | boulders (steel pickaxe or better), meteorites (gold pickaxe or better), mine boulders | pickaxe |

Returns `{"cleared": n, "left": k}`, plus `"unreachable"` (at most 10) and `"why"`. After trees or stumps, it walks over the dropped wood and sap nearby to pick it up.

Raises:
- `TypeError` for a kind not in the table (`kinds can be weeds, twigs, ...`).
- `NotEnough` if a tool is missing (`you don't have a axe`) or energy runs out (`out of energy (cleared 12)`).

Gotchas:
- Large stumps, logs and boulders your tools can't break are silently left out: they aren't counted in `left` either. Check `find("big_debris")` and your tools' `.upgrade` (`docs("items#Item fields")`).
- Trees take many swings and a lot of energy; clear trees only when you need the space or the wood.
- `"trees"` doesn't include fruit trees.

```python
clear([55, 12, 70, 22])                                    # weeds, twigs, stones
clear([55, 12, 70, 22], ["weeds", "twigs", "stones", "trees", "grass"])
```

## chop
`chop(area=None, tiles=None)`

Takes the axe and swings it **once** at each tile. Good for twigs and saplings; a tree needs many swings, so use `clear(area, ["trees"])` for trees.

Returns `{"done": n, "of": m}`, plus `"unreachable"` and `"why"`.

## break_rocks
`break_rocks(area=None, tiles=None)`

Takes the pickaxe and swings it **once** at each tile: enough for small stones; boulders need `clear(area, ["boulders"])`.

Returns `{"done": n, "of": m}`, plus `"unreachable"` and `"why"`.

```python
break_rocks(tiles=[[61, 16], [63, 17]])
```

## Paths blocked by debris
When a walk fails because debris is in the way, the error names the tiles to clear: `debris is in the way, clear [[30, 40], [31, 40]] first`. Clear an area around them and try again. See `docs("moving#go")`.

# Clearing
Clearing ground for crops: which kinds to gather, the tools big debris needs, and paths blocked by debris.

## Clearing a plot
Clearing is `gather` with the debris kinds and an area (`docs("gather")`):

```python
plot = Area(55, 12, 70, 22)
gather(["weeds", "twigs", "stone"], plot)   # the small debris: cheap
gather("debris", plot)                      # everything, trees and grass too
```

The wood, stone, fiber and sap they drop are picked up as it goes, and counted in `"got"`.

## Big debris and tools
| debris | needs |
|---|---|
| large stumps | copper axe or better |
| large logs | steel axe or better |
| boulders | steel pickaxe or better |
| meteorites | gold pickaxe or better |

What your tools can't break yet is left out, and `why_left` says how many tiles. Tools are upgraded at the blacksmith (`docs("items/shops")`); a tool's level is its `.upgrade` (`docs("items#Item fields")`).

## Gotchas
- Trees take many swings and a lot of energy; clear them only when you need the space or the wood. `"trees"` never includes fruit trees.
- Each swing of the axe or pickaxe costs energy; the scythe is free (`docs("farming#Energy")`).

## Paths blocked by debris
When a walk fails because debris is in the way, the error names the tiles to clear: `debris is in the way, clear [[30, 40], [31, 40]] first`. Clear them with `gather([(30, 40), (31, 40)])` and try again. See `docs("moving#go")`.

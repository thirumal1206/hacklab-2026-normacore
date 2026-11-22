- Object catalog (agent/config/objects.json, agent/catalog.py): live vision measures each object's colour and labels it
  instantly from the catalog by colour and size; `catalog.py add "<name>" <category>` teaches an entry from the table.
  Claude only checks in the background, on zoomed per-object tiles, choosing a catalog name; the image now goes inline
  (no Read tool turn, about 4-7 s instead of 8-14 s), and CLI calls no longer wait 3 s for stdin. Friday sees the colour.

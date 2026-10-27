# GeoSegment AI
 
AI-powered segmentation and detection of objects in rasters and basemaps, directly inside QGIS. Draw a zone, describe what you are looking for (or show a few examples) and get clean polygons back as a vector layer.
 
## What it does
 
GeoSegment AI adds a dock panel to QGIS where you pick an area of interest on a local raster (GeoTIFF, JPG) or on an online basemap and let a Segment Anything–based model find the objects you care about. You can describe the target in plain text, for example "house", or several classes at once such as "house, tree, swimming pool", or you can draw a few example polygons and let the model find similar objects across the whole zone. Large areas are tiled automatically and merged back into a single seamless result, so you do not have to split the work yourself.
 
Results arrive as a regular QGIS vector layer that you can style, edit, export and use in any other workflow. Each polygon carries a confidence value, and you can filter the output by confidence and by size in square metres. For buildings and other man-made objects the plugin can straighten shapes to right angles, fill holes and merge touching pieces, which removes most of the manual cleanup. A detection mode returns bounding boxes with class labels and confidence scores when you only need to locate and count objects rather than outline them.
 
When the model misses something or draws something wrong, you can fix it without leaving the panel. Click a polygon to correct it, click where the AI missed to add the missing object, or use the manual editing tools and the quick review mode to go through the results one by one. Finished layers can be exported to common vector formats.
 
## Installation
 
Install it from the QGIS Plugin Manager by searching for "GeoSegment AI". Because the plugin is currently flagged as experimental, you need to enable "Show also experimental plugins" in the Plugin Manager settings first. QGIS 3.22 or newer is required. To install manually, download the ZIP from the releases page and use "Install from ZIP" in the Plugin Manager.
 
## Getting started
 
Open the panel from the GeoSegment AI toolbar icon or the Plugins menu and sign in. Sign-in opens a page in your browser and returns you to QGIS automatically. Choose a raster layer or the built-in basemap, draw the zone you want to analyse, type what to find or draw a few examples, and run. Adjust precision, confidence and size filters afterwards without running the model again, then review and export the polygons.
 
## Account, quota and privacy
 
The segmentation itself runs on our cloud servers, so the plugin needs an internet connection and a free account. The part of the image inside your drawn zone, and only that part, is uploaded for processing and is used to produce your result. Usage is measured in square kilometres of processed area. New accounts get a free one-time allowance of 5 km², and larger quotas are available as paid packs on the website. See the website for the full terms, privacy policy and pricing.
 
## Support and bug reports
 
Please report bugs and request features in the [issue tracker](https://github.com/ORGANIZATION/REPOSITORY/issues). Including your QGIS version, operating system and a short description of the steps that led to the problem makes it much easier to reproduce.
 
## Development
 
The plugin is plain Python using the QGIS and PyQt APIs, with GDAL for raster preparation. To work on it, clone the repository and link the `sam_seg` folder into your QGIS profile's `python/plugins` directory, then use the Plugin Reloader plugin to pick up changes without restarting QGIS. The plugin talks to a remote API whose address can be overridden through the `sam_seg/api_url` setting, which is useful for pointing a development build at a test backend.
 
## License
 
Released under the GNU General Public License v2.0 or later, see [LICENSE](LICENSE).
 
## Links
 
Website: https://geosegmentai.com
Plugin page: https://plugins.qgis.org/plugins/sam_seg/
 

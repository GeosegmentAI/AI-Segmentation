def classFactory(iface):
    from .plugin import SamSegPlugin
    return SamSegPlugin(iface)

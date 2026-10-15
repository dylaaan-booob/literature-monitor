// Development build only. Browser/runtime users load the checked-in bundle.
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
const root=new URL('../',import.meta.url);
await build({
  entryPoints:[fileURLToPath(new URL('src/literature_monitor/web/static/ui_next/kept.js',root))],
  outfile:fileURLToPath(new URL('src/literature_monitor/web/static/ui_next/kept-app.js',root)),
  bundle:true,format:'esm',target:'es2022',legalComments:'eof',external:['*.woff2'],
  banner:{js:'// RhinE MIT + Blog Theme MIT adaptation. See SOURCES.md and licenses/.'},
});

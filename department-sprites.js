/* Versioned department GIF assets; four bounded static bundles, no new file API. */
(()=>{window.officeDepartmentSprites={};window.officeDepartmentSpriteVersion='2026-10-27-agent-personality-v2a';
for(const team of ['planning','design','development','research','godo-planning','godo-design','godo-development','godo-research']){const script=document.createElement('script');script.src='/department-sprites-'+team+'.js';script.onload=()=>dispatchEvent(new Event('office-department-sprites-ready'));document.head.append(script);}})();

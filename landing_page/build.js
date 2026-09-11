const { execSync } = require('child_process');
const fs = require('fs-extra');
const crypto = require('crypto');
const path = require('path');

const isPreview = process.argv.includes('--preview');
const isWatch = process.argv.includes('--watch');

function buildCss() {
  execSync(
    'npx @tailwindcss/cli -i src/input.css -o dist/output.css',
    { stdio: 'inherit', cwd: __dirname }
  );
}

function buildHtml(cssFilename = 'output.css') {
  const cacheControlMeta = `
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
  `;
  const htmlFiles = ['index.html', 'index-ru.html'];
  for (const htmlFile of htmlFiles) {
    let htmlContent = fs.readFileSync(path.join(__dirname, 'src', htmlFile), 'utf8');
    htmlContent = htmlContent.replace(/href="output\.css"/g, `href="${cssFilename}"`);
    htmlContent = htmlContent.replace(
      /(<meta charset="UTF-8">)/,
      `$1${cacheControlMeta}`
    );
    fs.writeFileSync(path.join(__dirname, 'dist', htmlFile), htmlContent);
  }
  if (fs.existsSync(path.join(__dirname, 'src', 'assets'))) {
    fs.copySync(path.join(__dirname, 'src', 'assets'), path.join(__dirname, 'dist', 'assets'));
  }
  if (fs.existsSync(path.join(__dirname, 'src', 'CNAME'))) {
    fs.copyFileSync(
      path.join(__dirname, 'src', 'CNAME'),
      path.join(__dirname, 'dist', 'CNAME')
    );
  }
  fs.copyFileSync(
    path.join(__dirname, 'src', 'sitemap.xml'),
    path.join(__dirname, 'dist', 'sitemap.xml')
  );
  fs.writeFileSync(
    path.join(__dirname, 'dist', '_headers'),
    `/*
  Cache-Control: no-cache, no-store, must-revalidate
  Pragma: no-cache
  Expires: 0

*.css
  Cache-Control: public, max-age=31536000, immutable

*.js
  Cache-Control: public, max-age=31536000, immutable

*.png
  Cache-Control: public, max-age=31536000, immutable

*.jpg
  Cache-Control: public, max-age=31536000, immutable

*.svg
  Cache-Control: public, max-age=31536000, immutable
`
  );
}

function runFullBuild() {
  if (!isPreview) {
    console.log('Cleaning up old CSS files...');
    const distDir = path.join(__dirname, 'dist');
    if (fs.existsSync(distDir)) {
      const files = fs.readdirSync(distDir);
      files.forEach(file => {
        if (file.startsWith('output.') && file.endsWith('.css') && file !== 'output.css') {
          fs.removeSync(path.join(distDir, file));
        }
      });
    }
  }

  console.log('Building CSS...');
  const outputPath = isPreview ? 'dist/output.css' : 'dist/temp.css';
  execSync(
    `npx @tailwindcss/cli -i src/input.css -o ${outputPath}${isPreview ? '' : ' --minify'}`,
    { stdio: 'inherit', cwd: __dirname }
  );

  let cssFilename = 'output.css';
  if (!isPreview) {
    const cssContent = fs.readFileSync(path.join(__dirname, 'dist/temp.css'));
    const cssHash = crypto.createHash('md5').update(cssContent).digest('hex').substring(0, 8);
    cssFilename = `output.${cssHash}.css`;
    fs.renameSync(
      path.join(__dirname, 'dist/temp.css'),
      path.join(__dirname, `dist/${cssFilename}`)
    );
  }

  buildHtml(cssFilename);
  console.log(`Built successfully! CSS file: ${cssFilename}`);
}

function watchAndRebuild() {
  const watched = [
    path.join(__dirname, 'src', 'input.css'),
    path.join(__dirname, 'src', 'index.html'),
    path.join(__dirname, 'src', 'index-ru.html'),
  ];
  let debounceTimer;
  const debounceMs = 150;

  function scheduleRebuild(filename) {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      const isCss = filename && filename.endsWith('.css');
      console.log(`[${new Date().toLocaleTimeString()}] Change detected, rebuilding...`);
      try {
        if (isCss || !filename) {
          buildCss();
        }
        buildHtml('output.css');
        console.log('Rebuild complete.');
      } catch (err) {
        console.error('Rebuild failed:', err.message);
      }
    }, debounceMs);
  }

  watched.forEach((filePath) => {
    if (fs.existsSync(filePath)) {
      fs.watch(filePath, (eventType, filename) => {
        scheduleRebuild(filename);
      });
    }
  });
  console.log('Watching CSS and HTML for changes...');
}

// Main
if (isWatch) {
  runFullBuild();
  watchAndRebuild();
} else {
  runFullBuild();
}

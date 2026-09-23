# Fantasy Football Live Tool

This complete npm package is designed for upload through the GitHub website. It intentionally uses `npm install` in GitHub Actions, so a package-lock.json file is not required.

## Upload
1. Extract this ZIP.
2. Open the `fantasy-football-live-tool` folder.
3. Upload the folder contents to the root of the GitHub repository.
4. Ensure `.github/workflows/deploy.yml`, `src`, `public`, `package.json`, `index.html`, and `vite.config.js` are present.
5. In GitHub, open Settings > Pages and choose GitHub Actions.
6. Open Actions > Deploy GitHub Pages > Run workflow.

The website initially uses demo data in `public/data/latest.json`. Never place paid API keys in front-end JavaScript.

with open("app.jsx", "r") as f:
    app = f.read()

import re

# find the Sidebar block
target = r"\{sidebarOpen && <Sidebar([^>]+)/>\}"
replacement = r"""<div style={{ overflow: "hidden" }}>
          <div style={{ width: 220, height: "100%" }}>
            <Sidebar\1/>
          </div>
        </div>"""

app = re.sub(target, replacement, app, flags=re.DOTALL)

with open("app.jsx", "w") as f:
    f.write(app)

print("Patch2 applied.")

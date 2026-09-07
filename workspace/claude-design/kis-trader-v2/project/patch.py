import os
import glob

# 1. Update app.jsx
with open("app.jsx", "r") as f:
    app = f.read()

app = app.replace('const [active, setActive] = useState("dashboard");', 'const [active, setActive] = useState("dashboard");\n  const [sidebarOpen, setSidebarOpen] = useState(true);\n  const toggleSidebar = () => setSidebarOpen(o => !o);')

app = app.replace('view = <ViewDashboard data={data} />;', 'view = <ViewDashboard data={data} onToggleSidebar={toggleSidebar} />;')
app = app.replace('view = <ViewAccount data={data} />;', 'view = <ViewAccount data={data} onToggleSidebar={toggleSidebar} />;')
app = app.replace('view = <ViewOrders data={data} />;', 'view = <ViewOrders data={data} onToggleSidebar={toggleSidebar} />;')
app = app.replace('view = <ViewTrace data={data} />;', 'view = <ViewTrace data={data} onToggleSidebar={toggleSidebar} />;')
app = app.replace('view = <ViewApi data={data} />;', 'view = <ViewApi data={data} onToggleSidebar={toggleSidebar} />;')

app = app.replace('<div className="kt-body">', '<div className="kt-body" style={{ gridTemplateColumns: sidebarOpen ? "220px 1fr" : "0 1fr" }}>')
app = app.replace('<Sidebar', '{sidebarOpen && <Sidebar')
app = app.replace('host={data.ENGINE.host}\n        />', 'host={data.ENGINE.host}\n        />}')

with open("app.jsx", "w") as f:
    f.write(app)

# 2. Update all view*.jsx
for view_file in glob.glob("view*.jsx"):
    with open(view_file, "r") as f:
        content = f.read()
    
    # replace ViewXXX({ data }) with ViewXXX({ data, onToggleSidebar })
    import re
    content = re.sub(r'(function View[A-Za-z0-9_]+)\(\{\s*data\s*\}\)', r'\1({ data, onToggleSidebar })', content)
    
    # replace <Toolbar title="XXX"> with <Toolbar title="XXX" onToggleSidebar={onToggleSidebar}>
    # note: some might have crumb or other props
    content = re.sub(r'(<Toolbar\s+[^>]*?)>', r'\1 onToggleSidebar={onToggleSidebar}>', content)
    
    with open(view_file, "w") as f:
        f.write(content)

# 3. Update ui.jsx Toolbar
with open("ui.jsx", "r") as f:
    ui = f.read()

ui = ui.replace('function Toolbar({ title, crumb, meta, children }) {', 'function Toolbar({ title, crumb, meta, children, onToggleSidebar }) {')

replacement = """    <div className="kt-toolbar">
      {onToggleSidebar && (
        <button className="kt-btn icon-only subtle" onClick={onToggleSidebar} style={{ marginRight: 8 }}>
          <Icon name="list" size={16} />
        </button>
      )}
      <div className="kt-toolbar-title">"""
ui = ui.replace('    <div className="kt-toolbar">\n      <div className="kt-toolbar-title">', replacement)

with open("ui.jsx", "w") as f:
    f.write(ui)

print("Patch applied successfully.")

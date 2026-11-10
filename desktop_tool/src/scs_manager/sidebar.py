"""Stable project nodes with expandable work-package children and activity ordering."""
from datetime import datetime

from PyQt5.QtCore import Qt, QSize
from PyQt5.QtWidgets import QStyle, QTreeWidget, QTreeWidgetItem


class ProjectTree(QTreeWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setIndentation(16)
        self.setColumnCount(1)
        self.setUniformRowHeights(False)
        self.setExpandsOnDoubleClick(True)

    # Keep the small flat-list accessors useful to callers/tests.
    def count(self):
        return self.topLevelItemCount()

    def item(self,index):
        return self.topLevelItem(index)

    def setCurrentRow(self,index):
        self.setCurrentItem(self.topLevelItem(index))

    def update_projects(self, entries, collapsed, opened):
        previous=self.currentItem()
        selected=(previous.data(0,Qt.UserRole),previous.data(0,Qt.UserRole+1)) if previous else None
        offset=self.verticalScrollBar().value()
        existing={self.topLevelItem(i).data(0,Qt.UserRole):self.topLevelItem(i) for i in range(self.count())}
        ordered=sorted(entries,key=lambda e:(-max(float(e.get('last_activity',0)),float(opened.get(e['root'],0))),e['root']))
        wanted={e['root'] for e in ordered}
        self.blockSignals(True)
        try:
            for root,item in list(existing.items()):
                if root not in wanted:
                    self.takeTopLevelItem(self.indexOfTopLevelItem(item))
            chosen=None
            for pos,entry in enumerate(ordered):
                root=entry['root']; item=existing.get(root)
                if item is None:
                    item=QTreeWidgetItem()
                    item.setData(0,Qt.UserRole,root)
                    item.setIcon(0,self.style().standardIcon(QStyle.SP_DirIcon))
                    self.insertTopLevelItem(pos,item)
                    item.setExpanded(not collapsed.get(root,False))
                elif self.indexOfTopLevelItem(item)!=pos:
                    expanded=item.isExpanded()
                    self.takeTopLevelItem(self.indexOfTopLevelItem(item));self.insertTopLevelItem(pos,item);item.setExpanded(expanded)
                last=max(float(entry.get('last_activity',0)),float(opened.get(root,0)))
                label=datetime.fromtimestamp(last).strftime('%m-%d %H:%M') if last else '暂无活动'
                title=entry['name']+'\n'+label
                if item.text(0)!=title:
                    item.setText(0,title)
                item.setToolTip(0,root+('\n'+entry['error'] if entry.get('error') else ''))
                item.setSizeHint(0,QSize(180,48))
                children={item.child(i).data(0,Qt.UserRole+1):item.child(i) for i in range(item.childCount())}
                ids={p['id'] for p in entry.get('packages',[])}
                for pid,child in children.items():
                    if pid not in ids:
                        item.removeChild(child)
                for i,package in enumerate(entry.get('packages',[])):
                    child=children.get(package['id'])
                    if child is None:
                        child=QTreeWidgetItem(); child.setData(0,Qt.UserRole,root);child.setData(0,Qt.UserRole+1,package['id'])
                        item.insertChild(i,child)
                    elif item.indexOfChild(child)!=i:
                        item.takeChild(item.indexOfChild(child));item.insertChild(i,child)
                    if child.text(0)!=package['goal']:
                        child.setText(0,package['goal'])
                    child.setToolTip(0,package['id']+' · '+package['state'])
                    child.setSizeHint(0,QSize(180,32))
                    if selected==(root,package['id']):
                        chosen=child
                if selected==(root,None):
                    chosen=item
            if chosen:
                self.setCurrentItem(chosen)
            self.verticalScrollBar().setValue(offset)
        finally:
            self.blockSignals(False)

    def filter_projects(self,value):
        value=value.casefold()
        for i in range(self.count()):
            item=self.item(i)
            match=value in (item.text(0)+' '+item.data(0,Qt.UserRole)).casefold()
            child_match=False
            for j in range(item.childCount()):
                child=item.child(j); visible=match or value in child.text(0).casefold()
                child.setHidden(not visible);child_match|=visible
            item.setHidden(not (match or child_match))

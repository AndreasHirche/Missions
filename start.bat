@echo off
python gen_knowledge_graph.py
start http://localhost:8080/knowledge_graph.html
python server.py

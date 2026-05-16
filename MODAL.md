# Modal Deployment Guide

## Projects
- **project-alpha** - Primary deployment
- **project-beta** - Staging/backup

## Setup

1. Install Modal CLI:
```bash
pip install modal
```

2. Set token (user will provide):
```bash
modal token set
```

3. Create projects:
```bash
modal env create project-alpha
modal env create project-beta
```

## Deploy

Deploy router:
```bash
modal deploy vllm-sr-router --env project-alpha
```

Deploy dashboard:
```bash
modal deploy vllm-sr-dashboard --env project-alpha
```

## URLs
- Router: https://project-alpha--vllm-sr-router.modal.cloud
- Dashboard: https://project-alpha--vllm-sr-dashboard.modal.cloud

# Render Deployment Guide for VenDOOR Marketplace Bot

This guide walks you through deploying the VenDOOR marketplace bot to Render.

## Prerequisites

1. GitHub account with your VenDOOR repository
2. Render account (https://render.com)
3. Telegram Bot Token from @BotFather
4. Korapay API credentials (public and secret keys)

## Deployment Steps

### 1. Push Code to GitHub

Make sure your code is pushed to GitHub:

```bash
git add .
git commit -m "Add render.yaml deployment configuration"
git push origin main
```

### 2. Connect GitHub to Render

1. Go to [Render Dashboard](https://dashboard.render.com)
2. Click on **Blueprints** in the left sidebar
3. Click **Create New Blueprint**
4. Select your GitHub repository
5. Authorize Render to access your GitHub account
6. Select the VenDOOR repository
7. Render will detect the `render.yaml` file automatically

### 3. Configure Environment Variables

Before deployment, set these environment variables in Render:

| Variable | Value | Required |
|----------|-------|----------|
| `TELEGRAM_BOT_TOKEN` | Get from @BotFather on Telegram | ✅ Yes |
| `KORAPAY_PUBLIC_KEY` | Get from Korapay merchant dashboard | ✅ Yes |
| `KORAPAY_SECRET_KEY` | Get from Korapay merchant dashboard | ✅ Yes |
| `DEBUG` | `false` | No (defaults to false) |
| `ALLOWED_HOSTS` | Will be auto-configured | No |
| `API_HOST` | Will be auto-configured | No |
| `ESCROW_RELEASE_HOURS` | `48` | No (defaults to 48) |

### 4. Deploy

1. In the Render dashboard, click **Deploy Blueprint**
2. Render will:
   - Create PostgreSQL database
   - Create Redis service
   - Deploy the API (web service)
   - Deploy the Telegram bot (background worker)
   - Deploy the Celery worker (background worker)
   - Deploy the Celery Beat scheduler (background worker)
   - Run database migrations automatically

3. Wait for all services to reach "Live" status (usually 3-5 minutes)

## Service Breakdown

### vendoor-api (Web Service)
- FastAPI application serving HTTP requests
- Health check endpoint: `/health`
- API endpoints for status checking and admin stats
- Receives Korapay webhooks

### vendoor-bot (Background Worker)
- Telegram bot using polling mode
- Handles user interactions
- Processes commands and callbacks

### vendoor-worker (Background Worker)
- Celery worker processes background tasks
- Handles escrow auto-release logic
- Processes scheduled notifications

### vendoor-beat (Background Worker)
- Celery Beat scheduler
- Runs periodic tasks (check escrows every hour, notifications every 15 mins)

### vendoor-db (Database)
- PostgreSQL 15 database
- Stores all marketplace data

### vendoor-redis (Cache)
- Redis instance
- Message broker for Celery
- Result backend for task status

## Monitoring

### View Logs

1. Click on each service in Render dashboard
2. Click the **Logs** tab to see real-time logs
3. Common logs to check:
   - API health check endpoint
   - Bot startup messages
   - Celery task execution
   - Database errors

### Common Issues

#### Bot not responding
- Check `vendoor-bot` service logs for errors
- Verify `TELEGRAM_BOT_TOKEN` is correct
- Bot uses polling mode; should stay running

#### Payments not processing
- Check `vendoor-api` logs for webhook errors
- Verify `KORAPAY_PUBLIC_KEY` and `KORAPAY_SECRET_KEY` are correct
- Test payment webhook in Korapay dashboard

#### Celery tasks not running
- Check `vendoor-worker` logs
- Check `vendoor-beat` logs for scheduler status
- Verify Redis connection works

## Updating Code

To update your deployment:

1. Make code changes locally
2. Push to GitHub:
   ```bash
   git add .
   git commit -m "Your update message"
   git push origin main
   ```
3. Render automatically redeploys when you push to main
4. Check Render dashboard to see build progress

## Scaling

To increase capacity:

1. In Render dashboard, click on each service
2. Go to **Settings** tab
3. Adjust plan tier (Starter, Standard, Pro)
4. Higher tiers provide more resources and reliability

## Database Backups

Render automatically backs up your PostgreSQL database. To restore:

1. Go to your database service
2. Click **Data & Backups**
3. Select a backup and restore

## Costs

- **PostgreSQL**: ~$7-25/month depending on usage
- **Redis**: ~$7-15/month depending on usage
- **Web Service**: $7-25/month depending on traffic
- **Background Workers**: ~$7 each/month

**Free tier alternative**: Use Render's free tier with limitations (database sleeps after inactivity)

## Custom Domain (Optional)

To use your own domain:

1. Get a domain from GoDaddy, Namecheap, etc.
2. In Render, go to your API service settings
3. Click **Custom Domains**
4. Add your domain
5. Update Korapay webhook URL to point to your custom domain

## Environment Variables in Render

Once deployed, you can update environment variables without redeploying:

1. Click on the service
2. Go to **Environment**
3. Click **Edit** next to the variable
4. Render will restart the service with the new value

## Rollback

If a deployment fails:

1. Go to the service
2. Click **Latest Deploys**
3. Click on a previous successful deployment
4. Click **Redeploy**

## Support

For issues with Render:
- Check [Render Documentation](https://render.com/docs)
- Email support@render.com

For issues with VenDOOR:
- Check bot logs in Render dashboard
- Verify environment variables are set correctly
- Test locally with `python bot/main.py`

## Verification Checklist

After deployment, verify:

- [ ] API health check responds at `/health`
- [ ] Bot responds to `/start` command on Telegram
- [ ] Bot can show main menu with buttons
- [ ] Database migrations ran successfully (check API logs)
- [ ] Redis connection works (check Celery worker logs)
- [ ] Celery Beat scheduler is running (check beat logs)

You're now live on Render! 🎉

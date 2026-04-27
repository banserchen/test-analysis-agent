pipeline {
  agent any

  options {
    timestamps()
    disableConcurrentBuilds()
    buildDiscarder(logRotator(numToKeepStr: '20'))
  }

  parameters {
    string(name: 'COMPOSE_FILE',         defaultValue: 'docker-compose.yml',                              description: 'Compose 文件路径')
    string(name: 'PROJECT_NAME',         defaultValue: 'test-analysis-agent',                             description: 'Compose project name / 镜像前缀')
    string(name: 'REMOTE_HOST',          defaultValue: '192.168.103.26',                                  description: '部署宿主机地址')
    string(name: 'REMOTE_DEPLOY_DIR',    defaultValue: '/opt/jenkins_home/workspace/Test-Analysis-Agent', description: '部署目录（宿主机上代码所在路径）')
    string(name: 'HOST_PORT',            defaultValue: '9090',                                            description: '宿主机映射端口')
    string(name: 'REMOTE_SSH_CREDENTIAL_ID',      defaultValue: 'jenkins-host-ssh-key',   description: 'SSH 私钥凭据ID（类型：SSH Username with private key）')
    string(name: 'LLM_API_KEY_CREDENTIAL_ID',     defaultValue: 'TAA_LLM_API_KEY',        description: 'Secret Text 凭据ID — LLM API Key (TAA_LLM_API_KEY)')
    string(name: 'JENKINS_TOKEN_CREDENTIAL_ID',   defaultValue: 'TAA_JENKINS_PASSWORD',   description: 'Secret Text 凭据ID — Jenkins API Token (TAA_JENKINS_PASSWORD)')
    string(name: 'FEISHU_SECRET_CREDENTIAL_ID',   defaultValue: 'TAA_FEISHU_APP_SECRET',  description: 'Secret Text 凭据ID — 飞书 App Secret (TAA_FEISHU_APP_SECRET)')
    string(name: 'HEALTHCHECK_URL',      defaultValue: 'http://localhost:9090/health',                    description: '在远端主机内执行的健康检查 URL')
    booleanParam(name: 'SKIP_DEPLOY',    defaultValue: false,                                             description: '仅构建镜像，不执行部署')
  }

  environment {
    DOCKER_BUILDKIT          = '1'
    COMPOSE_DOCKER_CLI_BUILD = '1'
  }

  stages {

    stage('Checkout') {
      steps {
        deleteDir()
        checkout scm
      }
    }

    stage('Validate Compose (Remote)') {
      steps {
        script {
          withCredentials([
            sshUserPrivateKey(credentialsId: params.REMOTE_SSH_CREDENTIAL_ID, keyFileVariable: 'SSH_KEY', usernameVariable: 'SSH_USER'),
            string(credentialsId: params.LLM_API_KEY_CREDENTIAL_ID,   variable: 'TAA_LLM_API_KEY'),
            string(credentialsId: params.JENKINS_TOKEN_CREDENTIAL_ID, variable: 'TAA_JENKINS_PASSWORD'),
            string(credentialsId: params.FEISHU_SECRET_CREDENTIAL_ID, variable: 'TAA_FEISHU_APP_SECRET')
          ]) {
            sh '''
              set -e
              SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no"
              ssh ${SSH_OPTS} ${SSH_USER}@${REMOTE_HOST} "
                set -e
                cd '${REMOTE_DEPLOY_DIR}'
                export PROJECT_NAME='${PROJECT_NAME}'
                export BUILD_NUMBER='${BUILD_NUMBER}'
                export IMAGE_TAG='${BUILD_NUMBER}'
                export HOST_PORT='${HOST_PORT}'
                export TAA_LLM_API_KEY='${TAA_LLM_API_KEY}'
                export TAA_JENKINS_PASSWORD='${TAA_JENKINS_PASSWORD}'
                export TAA_FEISHU_APP_SECRET='${TAA_FEISHU_APP_SECRET}'
                docker compose -f '${COMPOSE_FILE}' -p '${PROJECT_NAME}' config -q
              "
            '''
          }
        }
      }
    }

    stage('Build Image (Remote)') {
      steps {
        script {
          withCredentials([
            sshUserPrivateKey(credentialsId: params.REMOTE_SSH_CREDENTIAL_ID, keyFileVariable: 'SSH_KEY', usernameVariable: 'SSH_USER'),
            string(credentialsId: params.LLM_API_KEY_CREDENTIAL_ID,   variable: 'TAA_LLM_API_KEY'),
            string(credentialsId: params.JENKINS_TOKEN_CREDENTIAL_ID, variable: 'TAA_JENKINS_PASSWORD'),
            string(credentialsId: params.FEISHU_SECRET_CREDENTIAL_ID, variable: 'TAA_FEISHU_APP_SECRET')
          ]) {
            sh '''
              set -e
              SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no"
              ssh ${SSH_OPTS} ${SSH_USER}@${REMOTE_HOST} "
                set -e
                cd '${REMOTE_DEPLOY_DIR}'
                export PROJECT_NAME='${PROJECT_NAME}'
                export BUILD_NUMBER='${BUILD_NUMBER}'
                export IMAGE_TAG='${BUILD_NUMBER}'
                export HOST_PORT='${HOST_PORT}'
                export DOCKER_BUILDKIT='${DOCKER_BUILDKIT}'
                export COMPOSE_DOCKER_CLI_BUILD='${COMPOSE_DOCKER_CLI_BUILD}'
                export TAA_LLM_API_KEY='${TAA_LLM_API_KEY}'
                export TAA_JENKINS_PASSWORD='${TAA_JENKINS_PASSWORD}'
                export TAA_FEISHU_APP_SECRET='${TAA_FEISHU_APP_SECRET}'
                docker compose -f '${COMPOSE_FILE}' -p '${PROJECT_NAME}' build
              "
            '''
          }
        }
      }
    }

    stage('Deploy (Remote)') {
      when {
        expression { !params.SKIP_DEPLOY }
      }
      steps {
        script {
          withCredentials([
            sshUserPrivateKey(credentialsId: params.REMOTE_SSH_CREDENTIAL_ID, keyFileVariable: 'SSH_KEY', usernameVariable: 'SSH_USER'),
            string(credentialsId: params.LLM_API_KEY_CREDENTIAL_ID,   variable: 'TAA_LLM_API_KEY'),
            string(credentialsId: params.JENKINS_TOKEN_CREDENTIAL_ID, variable: 'TAA_JENKINS_PASSWORD'),
            string(credentialsId: params.FEISHU_SECRET_CREDENTIAL_ID, variable: 'TAA_FEISHU_APP_SECRET')
          ]) {
            sh '''
              set -e
              SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no"
              ssh ${SSH_OPTS} ${SSH_USER}@${REMOTE_HOST} "
                set -e
                cd '${REMOTE_DEPLOY_DIR}'
                export PROJECT_NAME='${PROJECT_NAME}'
                export BUILD_NUMBER='${BUILD_NUMBER}'
                export IMAGE_TAG='${BUILD_NUMBER}'
                export HOST_PORT='${HOST_PORT}'
                export TAA_LLM_API_KEY='${TAA_LLM_API_KEY}'
                export TAA_JENKINS_PASSWORD='${TAA_JENKINS_PASSWORD}'
                export TAA_FEISHU_APP_SECRET='${TAA_FEISHU_APP_SECRET}'
                docker compose -f '${COMPOSE_FILE}' -p '${PROJECT_NAME}' up -d --remove-orphans
              "
            '''
          }
        }
      }
    }

    stage('Health Check') {
      when {
        expression { !params.SKIP_DEPLOY }
      }
      steps {
        script {
          withCredentials([
            sshUserPrivateKey(credentialsId: params.REMOTE_SSH_CREDENTIAL_ID, keyFileVariable: 'SSH_KEY', usernameVariable: 'SSH_USER')
          ]) {
            sh '''
              set -e
              SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no"
              ssh ${SSH_OPTS} ${SSH_USER}@${REMOTE_HOST} "
                set +e
                for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
                  curl -fsS '${HEALTHCHECK_URL}' >/dev/null 2>&1 && exit 0
                  sleep 3
                done
                echo 'Health check failed: ${HEALTHCHECK_URL}'
                exit 1
              "
            '''
          }
        }
      }
    }
  }

  post {
    always {
      script {
        withCredentials([
          sshUserPrivateKey(credentialsId: params.REMOTE_SSH_CREDENTIAL_ID, keyFileVariable: 'SSH_KEY', usernameVariable: 'SSH_USER')
        ]) {
          echo '当前容器状态（远端主机）：'
          sh '''
            set -e
            SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no"
            ssh ${SSH_OPTS} ${SSH_USER}@${REMOTE_HOST} "
              cd '${REMOTE_DEPLOY_DIR}'
              docker compose -f '${COMPOSE_FILE}' -p '${PROJECT_NAME}' ps
            "
          '''
        }
      }
    }
    failure {
      script {
        withCredentials([
          sshUserPrivateKey(credentialsId: params.REMOTE_SSH_CREDENTIAL_ID, keyFileVariable: 'SSH_KEY', usernameVariable: 'SSH_USER')
        ]) {
          echo '部署失败，输出远端最近日志：'
          sh '''
            set +e
            SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no"
            ssh ${SSH_OPTS} ${SSH_USER}@${REMOTE_HOST} "
              cd '${REMOTE_DEPLOY_DIR}'
              docker compose -f '${COMPOSE_FILE}' -p '${PROJECT_NAME}' logs --tail=200
            "
          '''
        }
      }
    }
  }
}

